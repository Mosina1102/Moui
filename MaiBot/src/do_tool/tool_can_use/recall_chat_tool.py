"""话题回溯工具（方案K·长话题抗遗忘）：
长话题聊久了早期细节会滑出视野（中期记忆只剩一句话主题），
但对话原文全量躺在 MongoDB——这个工具让Moui能主动回查"这个话题之前聊了什么"。
"""
import asyncio
import re
import time
from typing import Any, Dict

from src.common.logger_manager import get_logger
from ..tool_context import get_chat_id
from .base_tool import BaseTool

logger = get_logger("recall_chat")

_last_call_time = 0.0
COOLDOWN_SECONDS = 30


def _escape_keyword(kw: str) -> str:
    """转义正则特殊字符，防止 LLM 传进来的关键词炸掉查询"""
    return re.escape(kw.strip())


def _query_history(chat_id, keywords: list, limit: int) -> list:
    from ...common.database import db

    pattern = "|".join(_escape_keyword(k) for k in keywords if k.strip())
    if not pattern:
        return []
    docs = db.messages.find(
        {
            "chat_id": chat_id,
            "processed_plain_text": {"$regex": pattern, "$options": "i"},
        }
    ).sort("time", -1).limit(max(1, min(20, limit)))

    rows = []
    for doc in docs:
        user_info = doc.get("user_info") or {}
        nickname = doc.get("group_nickname") or user_info.get("user_nickname") or "某人"
        ts = doc.get("time", 0)
        try:
            time_str = time.strftime("%m-%d %H:%M", time.localtime(float(ts)))
        except (TypeError, ValueError):
            time_str = "时间未知"
        rows.append({"time": time_str, "name": str(nickname), "text": str(doc.get("processed_plain_text", ""))[:150]})
    return rows


class RecallChatTool(BaseTool):
    name = "recall_chat"
    description = (
        "回看本群/本私聊之前的聊天记录原文。当你觉得这个话题之前聊过、"
        "需要回忆讨论到哪一步了、或者想确认之前谁说过什么的时候使用。"
        "keywords 传1~3个话题关键词（如：游戏,抽卡）。返回相关消息原文（按时间排好）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "keywords": {
                "type": "string",
                "description": "话题关键词，多个用逗号分隔，如：原神,抽卡",
            },
            "limit": {"type": "integer", "description": "最多返回几条，默认8"},
        },
        "required": ["keywords"],
    }

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        global _last_call_time

        # 优先显式传参（PFC/统一大脑塞进 function_args 的），回落上下文
        chat_id = function_args.get("chat_id") or get_chat_id()
        if chat_id is None:
            return {"status": "failed", "message": "当前上下文拿不到聊天ID，无法回查"}

        keywords_raw = str(function_args.get("keywords") or "").strip()
        if not keywords_raw:
            return {"status": "failed", "message": "keywords 不能为空"}
        keywords = [k for k in re.split(r"[,，、\s]+", keywords_raw) if k.strip()]
        if not keywords:
            return {"status": "failed", "message": "关键词解析失败"}

        remain = COOLDOWN_SECONDS - (time.time() - _last_call_time)
        if remain > 0:
            return {"status": "failed", "message": f"刚回查过，冷却中（{remain:.0f}秒）"}

        try:
            limit = int(function_args.get("limit") or 8)
        except (TypeError, ValueError):
            limit = 8

        rows = await asyncio.to_thread(_query_history, chat_id, keywords, limit)
        _last_call_time = time.time()

        if not rows:
            return {"status": "ok", "message": f"没找到和「{keywords_raw}」相关的历史消息（可能话题太旧已被清理，或关键词太具体）"}

        # 按时间正序排列，还原对话脉络
        rows.reverse()
        lines = [f"[{r['time']}] {r['name']}：{r['text']}" for r in rows]
        return {
            "status": "ok",
            "message": f"「{keywords_raw}」的相关历史消息（{len(rows)}条，时间正序）：\n" + "\n".join(lines),
        }
