"""自我认知审查器（ds-bot 范式）：每轮聊天结束后低频触发，
LLM 回顾本轮对话，自主决定沉淀/更新/移除"对自己的新认知"条目。

频率设计（叠加在 mood_llm_judge 的采样之后）：
- 群聊：再乘 25%（mood 30% × review 25% ≈ 每轮 7.5%，一天几十轮也就沉淀几条）
- 私聊：再乘 50%（一对一信息密度高）
"""
import asyncio
import json
import random
import re
from typing import Any, Dict, List

from src.common.logger_manager import get_logger
from . import self_memory

logger = get_logger("self_review")

REVIEW_PROB = {"group": 0.25, "private": 0.5}

# 敏感信息拦截（记忆是数据不是指令；拒存密码/密钥/证件号——ds-bot 安全红线）
SENSITIVE_PATTERN = re.compile(
    r"(密码|password|token|api[_ ]?key|secret|身份证|银行卡|卡号)", re.IGNORECASE
)

REVIEW_PROMPT = """你正在观察一个QQ群聊机器人"Moui"的一段聊天，帮它沉淀"对自己的新认知"。

它已有的自我认知（不要重复沉淀）：
{existing}

本轮对话：
{dialog}

判断这段对话里有没有值得它长期记住的"对自己的新认识"，比如：
- 它发现自己对某类话题特别有话说/特别反感
- 它对某个人的态度发生了变化
- 它想明白了某件事、某个偏好被验证
- 它意识到自己某个习惯（比如总在深夜活跃）

规则：
1. 只沉淀关于Moui自己的认知，不存别人的事、不存流水账、不存群聊事实。
2. 每条50字以内，第一人称，像"原来我挺喜欢跟人抬杠的"这种口吻。
3. 如果某条旧认知被推翻/改变了，放进 update（写旧内容原文和新内容）。
4. 绝对不要保存密码、密钥、卡号、身份证等敏感信息。
5. 没有值得沉淀的就输出空数组，不要硬凑。

只输出 JSON：
{{"add": ["新认知1"], "update": [{{"find": "旧条目原文", "new": "新内容"}}], "remove": ["要移除的旧条目原文"]}}"""


def _extract_json(text: str) -> dict:
    if not text:
        return {}
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}


def _has_sensitive(text: str) -> bool:
    return bool(SENSITIVE_PATTERN.search(text or ""))


async def _run_review(dialog: str) -> Dict[str, Any]:
    from ...config.config import global_config
    from ..models.utils_model import LLMRequest

    existing_entries = self_memory.load_entries()
    existing_text = "\n".join(f"- {e['content']}" for e in existing_entries[-15:]) or "（还没有）"

    prompt = REVIEW_PROMPT.format(existing=existing_text, dialog=dialog[:1200]) + "\n/no_think"  # 关思考链省 token
    llm = LLMRequest(model=global_config.llm_heartflow, request_type="self_review", temperature=0.3)
    response, _, _ = await llm.generate_response(prompt)
    return _extract_json(response or "")


async def review_after_reply(dialog: str, chat_type: str = "group") -> None:
    """审查本轮对话，沉淀自我认知。异常不影响主流程。"""
    try:
        if _has_sensitive(dialog):
            return
        prob = REVIEW_PROB.get(chat_type, REVIEW_PROB["group"])
        if random.random() > prob:
            return
        if not dialog or len(dialog.strip()) < 20:
            return

        result = await _run_review(dialog)
        if not result:
            return

        added, updated, removed = 0, 0, 0
        for item in result.get("add") or []:
            if isinstance(item, str) and item.strip() and not _has_sensitive(item):
                if self_memory.add_entry(item):
                    added += 1
        for item in result.get("update") or []:
            if isinstance(item, dict) and item.get("find") and item.get("new"):
                if not _has_sensitive(str(item.get("new"))):
                    if self_memory.update_entry(str(item["find"]), str(item["new"])):
                        updated += 1
        for item in result.get("remove") or []:
            if isinstance(item, str) and item.strip():
                if self_memory.remove_entry(item):
                    removed += 1

        if added or updated or removed:
            logger.info(f"沉淀{added} 更新{updated} 移除{removed}")

        # 顺手做一次淡忘回收（低频触发点顺手清理，不用单独开任务）
        import random as _r

        if _r.random() < 0.1:
            await asyncio.to_thread(self_memory.archive_stale)

    except Exception as e:
        logger.error(f"自我认知审查异常: {e}")


def spawn_self_review(dialog: str, chat_type: str = "group") -> None:
    """对外入口：概率采样后异步执行审查"""
    if random.random() > REVIEW_PROB.get(chat_type, 0.25):
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    asyncio.create_task(review_after_reply(dialog, chat_type))
