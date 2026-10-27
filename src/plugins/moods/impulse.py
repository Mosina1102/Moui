"""行为冲动层：情绪事件 → 掷骰 → 产生行为冲动 → 注入 prompt，由Moui在回复前思考中自主执行。

参照 DSH 的"事件驱动+概率"哲学：冲动只在心情波动达到阈值时按概率产生（安静时段不出冲动），
真正的执行仍由Moui自己决定（工具各自冷却兜底，不会重复执行）。
链路：mood_llm_judge 判心情 → maybe_generate_impulse 掷骰 → MoodManager.pending_impulse → get_prompt 注入。
"""
import random
import time
from typing import List, Optional, Tuple

from src.common.logger_manager import get_logger

logger = get_logger("impulse")

# 触发参数（对齐 DSH 风格：事件驱动 + 概率掷骰，可按需调整）
POSITIVE_DELTA = 0.15  # 心情正向波动达到该值才可能触发积极冲动
NEGATIVE_DELTA = -0.15  # 负向波动阈值
# 概率分场景：私聊一对一情绪浓度高，冲动概率上调；群聊保守
PROBS = {
    "group": {"positive": 0.4, "negative": 0.3},
    "private": {"positive": 0.5, "negative": 0.4},
}
QUIET_START = 1  # 安静时段 1-8 点不产生冲动（对齐 DSH speak-up）
QUIET_END = 8

# 冲动池：(注入给Moui的内心话, 对应工具名)
POSITIVE_IMPULSES: List[Tuple[str, str]] = [
    ("有点想发个空间记录一下", "post_qzone"),
    ("今天心情不错，想换个头像", "change_avatar"),
    ("心情挺好，想换个签名", "change_signature"),
]
NEGATIVE_IMPULSES: List[Tuple[str, str]] = [
    ("心里有点闷，想发个空间说出来", "post_qzone"),
    ("心情不好，想换个签名", "change_signature"),
]


def _tool_ready(tool_name: str) -> bool:
    """检查对应工具是否在冷却中（冲动只在工具可用时才产生，避免白注入）"""
    try:
        if tool_name in ("change_avatar", "change_signature", "change_nickname", "send_poke"):
            from ...do_tool.tool_can_use.account_action_common import check_cooldown

            allowed, _ = check_cooldown(tool_name)
            return allowed
        if tool_name == "post_qzone":
            from ...do_tool.tool_can_use.qzone_tool import _check_cooldown

            allowed, _ = _check_cooldown()
            return allowed
    except Exception as e:
        logger.error(f"冲动冷却检查失败: {e}")
    return False


def maybe_generate_impulse(valence_change: float, chat_type: str = "group") -> None:
    """心情判断后调用：按概率产生行为冲动并写入 MoodManager.pending_impulse（分群聊/私聊概率）"""
    # 安静时段不出冲动
    hour = time.localtime().tm_hour
    if QUIET_START <= hour < QUIET_END:
        return

    probs = PROBS.get(chat_type, PROBS["group"])
    if valence_change >= POSITIVE_DELTA:
        pool, prob, mood_word = POSITIVE_IMPULSES, probs["positive"], "高涨"
    elif valence_change <= NEGATIVE_DELTA:
        pool, prob, mood_word = NEGATIVE_IMPULSES, probs["negative"], "低落"
    else:
        return

    if random.random() > prob:
        return

    # 只选当前没在冷却的工具
    candidates = [(hint, tool) for hint, tool in pool if _tool_ready(tool)]
    if not candidates:
        return

    hint, tool = random.choice(candidates)

    from .moods import MoodManager

    mood_manager = MoodManager.get_instance()
    mood_manager.pending_impulse = {"tool": tool, "hint": hint, "ts": time.time()}
    logger.info(f"[冲动] 心情{mood_word}(valence{valence_change:+.2f})，产生冲动: {hint} → {tool}")
