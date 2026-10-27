"""PFC 元工具（统一大脑形态）：心情汇报与自我认知沉淀由主模型在回复循环中顺带产出，
取代独立的 mood_llm_judge / reviewer 调用（私聊链路）。数据管线与群聊共用。
"""
import os
import time
from typing import Any, Dict

from src.common.logger_manager import get_logger

logger = get_logger("pfc_meta")

# 元工具定义（OpenAI function calling 格式，供主模型调用）
META_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "report_mood",
            "description": "（内部）每次回复结束时调用一次：汇报这段对话后你的心情变化。没有波动就不要调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "mood_text": {"type": "string", "description": "两三个字的心情词，如：开心/烦躁/平静"},
                    "valence_change": {"type": "number", "description": "愉悦度变化，-0.3到0.3"},
                    "arousal_change": {"type": "number", "description": "唤醒度变化，-0.3到0.3"},
                    "reason": {"type": "string", "description": "一句话说明刚才心里那一下，30字内"},
                },
                "required": ["mood_text", "valence_change", "arousal_change"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "note_insight",
            "description": (
                "（内部）当你在这段对话中对自己有了新的认识（习惯/喜好/态度变化/想法）时调用，"
                "沉淀为长期自我认知。只记录关于你自己的，50字内，没有就不要调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "第一人称的自我认知，50字以内"},
                    "importance": {"type": "number", "description": "重要程度0~1，默认0"},
                },
                "required": ["content"],
            },
        },
    },
]

META_TOOL_NAMES = {"report_mood", "note_insight"}


async def handle_meta_tool(name: str, args: Dict[str, Any], chat_type: str = "private") -> Dict[str, Any]:
    """处理主模型发起的元工具调用。返回 tool role 消息内容。chat_type 仅用于时间线标记。"""
    from ...config.config import global_config
    from ..moods.moods import MoodManager
    from ..self_growth import self_memory

    if name == "report_mood":
        mood_manager = MoodManager.get_instance()
        mood_text = str(args.get("mood_text", "") or "").strip() or "平静"
        try:
            valence_change = max(-0.3, min(0.3, float(args.get("valence_change", 0) or 0)))
            arousal_change = max(-0.3, min(0.3, float(args.get("arousal_change", 0) or 0)))
        except (TypeError, ValueError):
            valence_change, arousal_change = 0.0, 0.0
        reason = str(args.get("reason", "") or "").strip()

        # 判定偏正矫正 + 显著性门槛（feiyu-mood 同款规则）
        if abs(valence_change) < 0.03 and abs(arousal_change) < 0.03:
            return "心情变化不显著，已忽略"
        mood_manager.update_mood_from_text(
            mood_text, valence_change / 3.0, arousal_change / 3.0
        )
        if reason:
            mood_manager.last_reason = reason
            mood_manager.last_reason_time = time.time()
            mood_manager.last_reason_injected = False
        logger.info(f"{mood_text} ({valence_change:+.2f}, {arousal_change:+.2f}) | {reason}")
        try:
            os.makedirs("logs", exist_ok=True)
            with open("logs/mood_timeline.log", "a", encoding="utf-8") as f:
                f.write(
                    f"[{time.strftime('%m-%d %H:%M')}] [{chat_type}·元工具] valence{valence_change:+.2f} "
                    f"arousal{arousal_change:+.2f} reason={reason or '无'}\n"
                )
        except Exception:
            pass
        return "心情已记录"

    if name == "note_insight":
        content = str(args.get("content", "") or "").strip()[:120]
        if not content:
            return "空认知已忽略"
        try:
            importance = max(0.0, min(1.0, float(args.get("importance", 0) or 0)))
        except (TypeError, ValueError):
            importance = 0.0
        entry = self_memory.add_entry(content, importance=importance)
        if entry:
            logger.info(f"沉淀: {content[:50]}")
            return "自我认知已沉淀"
        return "该认知与已有条目重复，已刷新"

    return f"未知元工具: {name}"
