"""方案K层3：每日反思（Generative Agents Reflection 范式）。

由 plastic_scheduler 在 reflection_hour 触发。聚合层1打分流 → 提炼"经验"条目
（content 前缀 [经验]，category="experience"）→ 走 self_memory.add_entry 现成机制
（查重/遗忘曲线/容量回收；注入位配额见 self_memory.get_fresh_entries，experience ≤2 席）。
报告落盘 logs/reflection_YYYYMMDD.md 供老大巡检。

熔断：当日打分记录 < 5 条不调 LLM（数据不足不硬凑）。
坑⑫防线：反思 prompt 强制"倾向句式"禁绝对句（审核器偏差不固化成性格）。
坑⑭：remove 走模糊匹配（去空格双向 in，唯一命中才执行）。
"""
import json
import os
import time
from typing import Any, Dict, List, Optional

from src.common.logger_manager import get_logger
from src.config.config import global_config
from src.plugins.models.utils_model import LLMRequest
from src.plugins.PFC.pfc_utils import get_items_from_json

from . import score_events, self_memory

logger = get_logger("daily_reflection")

_MIN_EVENTS = 5  # 熔断阈值
_REPORT_DIR = "logs"

REFLECT_PROMPT = """你在帮QQ机器人"{bot_name}"做每日复盘。给它昨天的成绩单：审核打分流（哪些回复被打回/低分+原因）、心情时间线尾部、已有自我认知清单。

昨天成绩单（最差的在前）：
{report_block}

昨天心情尾部：
{mood_tail}

已有自我认知（不要重复沉淀）：
{existing}

请提炼"明天的她该记住的经验"，输出 JSON：
{{"add": ["第一人称经验条目1"], "remove": ["要推翻的旧条目原文"]}}

规则：
- 每条≤60字、第一人称，像"原来在这个群玩梗比讲道理受欢迎"这种口吻
- 必须从昨天证据里来，禁止空泛鸡汤；最多5条，没有就空数组
- 审核拒发只代表"当时不合适"（审核器自己也有偏差），经验优先总结成"在某场景我倾向怎么做"，禁止"永远不要做X"式绝对句——人不是规则执行的
- 与已有认知重复的不要；敏感信息（密码/密钥/卡号）绝对不存"""


def _read_mood_tail(n: int = 30) -> str:
    """读 logs/mood_timeline.log 尾部（写入方：meta_tools.report_mood 元工具，坑⑬已实证存在）。"""
    path = os.path.join(_REPORT_DIR, "mood_timeline.log")
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except FileNotFoundError:
        return "（暂无心情时间线）"
    except Exception as e:
        logger.error(f"读心情时间线失败: {e}")
        return ""


def _build_report_block(events: List[Dict[str, Any]]) -> tuple:
    """成绩单摘要：拒发优先 + 低分通过补充，最多 10 条。返回 (摘要文本, pass数, picked列表)。"""
    rejected = [e for e in events if not e.get("passed")]
    low_pass = [e for e in events if e.get("passed") and int(e.get("score", 5) or 5) <= 6]
    picked = (rejected + low_pass)[:10]
    pass_count = sum(1 for e in events if e.get("passed"))
    lines = [f"总条数 {len(events)}，通过 {pass_count} 条"]
    for e in picked:
        who = e.get("group_name") or e.get("stream_id") or "?"
        verdict = "拒发" if not e.get("passed") else f"通过(分{e.get('score')})"
        lines.append(f"- [{who}] {verdict} | {e.get('reply_head')} | 理由: {e.get('reason')}")
    return "\n".join(lines), pass_count, picked


def _fuzzy_find(entries: List[Dict[str, Any]], old: str) -> Optional[Dict[str, Any]]:
    """remove 模糊匹配（坑⑭）：去空格双向 in，唯一命中才返回。"""
    key = old.strip().replace(" ", "")
    if not key:
        return None
    hits = []
    for e in entries:
        content = str(e.get("content", "")).replace(" ", "")
        if key in content or content in key:
            hits.append(e)
    return hits[0] if len(hits) == 1 else None


def _write_report(
    events: List[Dict[str, Any]], pass_count: int, picked: List[Dict[str, Any]],
    added_texts: List[str], removed_count: int,
) -> None:
    try:
        os.makedirs(_REPORT_DIR, exist_ok=True)
        path = os.path.join(_REPORT_DIR, f"reflection_{time.strftime('%Y%m%d')}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# 每日反思 {time.strftime('%Y-%m-%d %H:%M')}\n\n")
            f.write(f"- 打分记录：{len(events)} 条，通过 {pass_count} 条\n\n")
            f.write("## 被打回/低分 top\n")
            for e in picked:
                who = e.get("group_name") or e.get("stream_id") or "?"
                verdict = "拒发" if not e.get("passed") else f"通过(分{e.get('score')})"
                f.write(f"- [{who}] {verdict} | {e.get('reply_head')} | {e.get('reason')}\n")
            f.write(f"\n## 新增经验（{len(added_texts)} 条）\n")
            for t in added_texts:
                f.write(f"- {t}\n")
            f.write(f"\n## 推翻旧认知（{removed_count} 条）\n")
    except Exception as e:
        logger.error(f"报告落盘失败: {e}")


async def run_daily_reflection() -> dict:
    """每日反思主入口。返回 {"added": int, "removed": int, "skipped": str|None}。"""
    try:
        events = score_events.get_recent(days=1)
        if len(events) < _MIN_EVENTS:
            return {"added": 0, "removed": 0, "skipped": "events<5"}

        report_block, pass_count, picked = _build_report_block(events)
        mood_tail = _read_mood_tail(30)

        entries = self_memory.load_entries()
        existing_text = "\n".join(f"- {e['content']}" for e in entries[-15:]) or "（还没有）"

        bot_name = getattr(global_config, "BOT_NICKNAME", "") or "Moui"
        prompt = REFLECT_PROMPT.format(
            bot_name=bot_name, report_block=report_block, mood_tail=mood_tail, existing=existing_text
        ) + "\n/no_think"

        llm = LLMRequest(
            model=global_config.llm_summary,
            request_type="daily_reflection",
            temperature=0.4,
            max_tokens=600,
        )
        response, _, _ = await llm.generate_response(prompt)
        content = response or ""

        ok, data = get_items_from_json(
            content,
            "每日反思",
            "add",
            "remove",
            default_values={"add": [], "remove": []},
            allow_array=True,
        )
        if not ok:
            logger.warning("输出解析失败，跳过本次沉淀")
            return {"added": 0, "removed": 0, "skipped": "parse_fail"}

        added_texts: List[str] = []
        for item in data.get("add") or []:
            if isinstance(item, str) and item.strip() and len(item.strip()) >= 6:
                text = f"[经验] {item.strip()[:60]}"
                if self_memory.add_entry(text, importance=0.2, category="experience"):
                    added_texts.append(text)
                else:
                    added_texts.append(text + "（与已有条目重复，已刷新锚点）")

        removed_count = 0
        for old in data.get("remove") or []:
            if isinstance(old, str) and old.strip():
                target = _fuzzy_find(entries, old)
                if target is not None:
                    if self_memory.remove_entry(target["content"]):
                        removed_count += 1
                else:
                    logger.info(f"remove 无唯一命中，跳过: {str(old)[:30]}")

        _write_report(events, pass_count, picked, added_texts, removed_count)
        logger.info(f"沉淀 {len(added_texts)} 条，推翻 {removed_count} 条")
        return {"added": len(added_texts), "removed": removed_count, "skipped": None}
    except Exception as e:
        logger.error(f"每日反思异常: {e}")
        return {"added": 0, "removed": 0, "skipped": f"error:{e}"}
