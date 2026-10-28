"""方案K层2：群像每日审查器（范式照抄 self_growth/reviewer.py + group_reply_checker 的 LLM 用法）。

由 plastic_scheduler 每群每日调用一次 review_group。本模块不自带循环。

v2.2 关键防线：
- 素材窗口真实对齐 24h（新坑C）：limit=500 + ts 后过滤（get_raw_msg_between_time 不存在，别用）
- 证据真实性（坑⑩）：evidence 前 12 字必须为素材子串，编造整体拒绝
- 反自证（新坑B）：evidence 命中行若是她自己的发言 → 无效证据
- prompt 反自证条款：档位判定只看群内容吸引力，不看她的发言量
"""
import json
import re
import time

from src.common.logger_manager import get_logger
from src.config.config import global_config
from src.plugins.models.utils_model import LLMRequest
from src.plugins.PFC.pfc_utils import get_items_from_json

from . import group_impression

logger = get_logger("group_review")

_DIALOG_CAP = 6000  # 新坑C：素材上限放宽
_MIN_DIALOG_LEN = 30

REVIEW_PROMPT = """你在帮QQ机器人"{bot_name}"维护它对一个群的长期印象。给它近一天（过去24小时）的群聊记录，输出认知更新。

已有印象（不要无脑重复，变了才更新）：
{existing}

近一天群聊：
{dialog}

只输出 JSON：{{"group_type": "群类型，≤10字", "atmosphere": "氛围一句话", "my_attitude": 1-5整数（这是"在这个群的表现策略"档位，不是情绪：1潜水看戏/2正常参与/3挺爱逛/4爱掺和/5主场）, "topics": ["主题1", "主题2"], "quirks": ["在这群的相处注意/梗风格"], "evidence": "支撑本次更新的一句群里真实原话"}}

重要——档位判定依据：my_attitude 反映的是【群内容对{bot_name}的吸引力】（话题类型、聊天质量、有没有聊得来的人），【不是她自己的发言量】。她某天发言少可能只是当时没好话题，禁止以"她今天没怎么说话"作为降档依据。

规则：evidence 必须一字不差地摘自上面群聊记录里【群友说的话】（禁止摘{bot_name}自己说过的话作为态度证据，系统会做子串与身份校验，编造会被整体拒绝）；没有依据就不要改对应字段；my_attitude 只能缓变；没有值得更新的就输出 evidence="无变化"。"""

_LINE_HEAD_RE = re.compile(r"^\[[^\]]*\]\s*")


def _line_speaker(line: str) -> str:
    """从 readable 行提取发送者名（容忍时间戳前缀与全/半角冒号、"说:"格式）。"""
    line = _LINE_HEAD_RE.sub("", line).strip()
    for sep in ("说：", "说:", "：", ":"):
        if sep in line:
            return line.split(sep, 1)[0].strip()
    return ""


def _is_bot_line(line: str, bot_name: str) -> bool:
    if not bot_name:
        return False
    speaker = _line_speaker(line)
    return speaker == bot_name or speaker.startswith(bot_name)


def _find_line_with(dialog: str, probe: str) -> str:
    for line in dialog.splitlines():
        if probe in line:
            return line
    return ""


async def review_group(stream_id: str, group_name: str) -> bool:
    """单群每日审查。返回 True=完成（含无变化/证据拒绝等正常跳过），False=可重试失败（LLM 429/网络/解析）。

    失败信号供调度器做 60s→300s 退避重试（429 时下个窗口 tick 自动补跑）。
    """
    try:
        from src.plugins.utils.chat_message_builder import (
            build_readable_messages,
            get_raw_msg_before_timestamp_with_chat,
        )

        now = time.time()
        # 新坑C：limit 必须 500（活跃群 120 条只覆盖 1-2 小时）；ts 后过滤对齐 24h 统计窗口
        msgs = get_raw_msg_before_timestamp_with_chat(chat_id=stream_id, timestamp=now, limit=500)
        msgs = [m for m in msgs if float(m.get("time", 0) or 0) >= now - 86400]
        if not msgs:
            return True
        dialog = await build_readable_messages(
            msgs, replace_bot_name=True, merge_messages=True, timestamp_mode="normal", read_mark=0.0
        )
        if not dialog or len(dialog.strip()) < _MIN_DIALOG_LEN:
            return True
        dialog = dialog[:_DIALOG_CAP]

        existing = group_impression.get_impression(stream_id) or {}
        existing_text = json.dumps(existing, ensure_ascii=False) if existing else "（还没有）"
        current_att = existing.get("my_attitude", 2) or 2

        bot_name = getattr(global_config, "BOT_NICKNAME", "") or "Moui"
        prompt = REVIEW_PROMPT.format(bot_name=bot_name, existing=existing_text, dialog=dialog) + "\n/no_think"
        llm = LLMRequest(
            model=global_config.llm_summary,
            request_type="group_review",
            temperature=0.3,
            max_tokens=400,
        )
        response, _, _ = await llm.generate_response(prompt)
        content = response or ""

        ok, data = get_items_from_json(
            content,
            "群像审查",
            "group_type",
            "atmosphere",
            "my_attitude",
            "topics",
            "quirks",
            "evidence",
            default_values={
                "group_type": existing.get("group_type", ""),
                "atmosphere": existing.get("atmosphere", ""),
                "my_attitude": current_att,
                "topics": existing.get("topics", []),
                "quirks": existing.get("quirks", []),
                "evidence": "",
            },
            allow_array=False,
            allow_empty=True,  # 个别字段缺省是合法的（apply_review 会再校验 evidence）
        )
        if not ok:
            logger.warning(f"审查输出解析失败: {group_name or stream_id}")
            return False  # 多半是429/网络抖动，供调度器退避重试

        evidence = str(data.get("evidence", "") or "").strip()
        if not evidence or evidence == "无变化":
            return True

        # 证据真实性校验（坑⑩）：前 12 字必须为素材子串
        probe = evidence[:12]
        if probe not in dialog:
            logger.warning(f"evidence 编造嫌疑，整体拒绝: {evidence[:30]}")
            return True

        # 反自证校验（新坑B）：evidence 命中行是她自己的发言 → 无效证据
        hit_line = _find_line_with(dialog, probe)
        if hit_line and _is_bot_line(hit_line, bot_name):
            logger.warning(f"evidence 摘自她自己的发言（自证），拒绝: {evidence[:30]}")
            return True

        updates = {
            "group_type": data.get("group_type"),
            "atmosphere": data.get("atmosphere"),
            "my_attitude": data.get("my_attitude", current_att),
            "topics": data.get("topics"),
            "quirks": data.get("quirks"),
        }
        group_impression.apply_review(stream_id, group_name, updates, evidence)
        return True
    except Exception as e:
        logger.error(f"审查异常 {group_name or stream_id}: {e}")
        return False  # 多半是429/网络抖动，供调度器退避重试
