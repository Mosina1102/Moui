import asyncio
import json
import os
import random
import time
from typing import List, Optional

from ...config.config import global_config
from ..models.utils_model import LLMRequest
from src.common.logger_manager import get_logger

logger = get_logger("mood_judge")

# 每轮回复后触发心情判断的概率（群聊高频场景下控制成本），0~1
MOOD_JUDGE_SAMPLE_RATE = 0.3
# 判定偏正矫正（feiyu-mood 的 judgeBiasDivisor 思路）：LLM 判定偏正（正的次数远多于负的），
# 衰减又会抹平波动——判定值除以该系数再应用，防止 valence 长期顶天花板
JUDGE_BIAS_DIVISOR = 3.0
# 显著性门槛（feiyu-mood 的 sigMood 思路）：涨跌太小的回合不应用，避免无意义的微抖动
SIGNIFICANT_DELTA = 0.03
# reason 注入有效期（小时）：超过这个时间的"心情理由"不再影响 prompt
REASON_VALID_HOURS = 2
# 时间线文件
TIMELINE_FILE = os.path.join("logs", "mood_timeline.log")

JUDGE_PROMPT = """你正在模拟一个QQ群聊机器人"Moui"的内心。刚才它在群里完成了一次回复，请根据以下信息判断它这一轮聊天后的心情变化。

{chat_content}

要求：
1. 站在Moui的视角感受这轮对话，判断它听完这些话后的心情往哪走。
2. valence_change 和 arousal_change 都在 -0.3 到 0.3 之间，波动幅度和事件的分量要匹配，鸡毛蒜皮的事就别大幅波动。
3. 心情下降只能因为：心疼某人、担心某事、自己真的难过。不允许因为"被惹生气/被冒犯"就大幅下降——真人不会每句话都往心里去。
4. reason 是一句话（30字内），用Moui的口吻描述"刚才心里那一下"，比如"刚才那句话有点戳我"。
5. confidence 表示你有多确定，0~1。拿不准就写低一点。

只输出 JSON，不要其他内容：
{{"mood_text": "两三个字的心情词", "valence_change": 0.0, "arousal_change": 0.0, "reason": "一句话", "confidence": 0.8}}"""


def _extract_json(text: str) -> Optional[dict]:
    """从 LLM 输出中提取 JSON 对象"""
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _append_timeline(line: str) -> None:
    try:
        os.makedirs("logs", exist_ok=True)
        with open(TIMELINE_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        logger.error(f"写入心情时间线失败: {e}")


async def judge_mood_after_reply(
    anchor_text: str, response_texts: List[str], chat_type: str = "group", speaker_uid: str = ""
) -> None:
    """回复完成后判断心情变化。非阻塞调用（create_task），异常不影响主流程。
    采样已在 spawn_mood_judge 处做过，这里不再重复掷骰。
    speaker_uid：锚点消息说话人（P7 按人加权心情用，空则权重1.0）。"""
    try:
        from ..moods.moods import MoodManager

        mood_manager = MoodManager.get_instance()

        reply_content = "\n".join(t for t in response_texts if t)[:500]
        if not anchor_text or not reply_content:
            return

        prompt = JUDGE_PROMPT.format(
            chat_content=f"对方说的：{anchor_text[:300]}\nMoui回复的：{reply_content}"
        ) + "\n/no_think"  # Qwen3.5 默认开思考链，判断类任务关掉省 token 提速

        llm = LLMRequest(model=global_config.llm_heartflow, request_type="mood_judge", temperature=0.3)
        response, _, _ = await llm.generate_response(prompt)

        result = _extract_json(response)
        if not result:
            logger.debug("心情判断：LLM 输出无法解析为 JSON，跳过")
            return

        confidence = float(result.get("confidence", 0) or 0)
        reason = str(result.get("reason", "") or "").strip()
        mood_text = str(result.get("mood_text", "") or "").strip()
        try:
            valence_change = max(-0.3, min(0.3, float(result.get("valence_change", 0) or 0)))
            arousal_change = max(-0.3, min(0.3, float(result.get("arousal_change", 0) or 0)))
        except (TypeError, ValueError):
            valence_change, arousal_change = 0.0, 0.0

        ts = time.strftime("%m-%d %H:%M:%S")
        _append_timeline(
            f"[{ts}] valence{valence_change:+.2f} arousal{arousal_change:+.2f} "
            f"conf={confidence:.2f} reason={reason or '无'}"
        )

        # 防呆：低置信度只记账不进嘴（不更新情绪、不注入理由）
        if confidence < 0.5:
            logger.debug(f"心情判断置信度低({confidence:.2f})，只记录不应用")
            return

        # 显著性门槛：微抖动不应用（feiyu"无变化的回合不记账"）
        if abs(valence_change) < SIGNIFICANT_DELTA and abs(arousal_change) < SIGNIFICANT_DELTA:
            logger.debug("心情变化不显著，跳过应用")
            return

        # P7 接线：按说话人好感加权应用心情波动（update_mood_by_user 内部 fail-open 权重1.0）
        mood_manager.update_mood_by_user(speaker_uid, valence_change / JUDGE_BIAS_DIVISOR, arousal_change / JUDGE_BIAS_DIVISOR)
        if reason:
            mood_manager.last_reason = reason
            mood_manager.last_reason_time = time.time()
            mood_manager.last_reason_injected = False  # 新理由：允许注入一次（feiyu"一次变化只说一次"）
        logger.info(f"[心情判断] {mood_text} ({valence_change:+.2f}, {arousal_change:+.2f}) | {reason}")

        # 行为冲动层：情绪波动大时按概率产生"想做什么"（写入 pending_impulse，注入后续 prompt）
        from ..moods.impulse import maybe_generate_impulse

        maybe_generate_impulse(valence_change, chat_type=chat_type)

        # 自我成长层（方案J）：低频审查本轮对话，沉淀"对自己的新认知"
        from ..self_growth.reviewer import spawn_self_review

        dialog = f"对方说的：{anchor_text}\nMoui回复的：{reply_content}"
        spawn_self_review(dialog, chat_type=chat_type)

    except Exception as e:
        logger.error(f"心情判断异常: {e}")


def spawn_mood_judge(
    anchor_text: str, response_texts: List[str], chat_type: str = "group", speaker_uid: str = ""
) -> None:
    """在回复流程结束后调用：按场景决定采样后异步执行，不阻塞发送流程。
    群聊按采样率（省 token）；私聊情绪浓度高，全量判断不采样。
    speaker_uid：锚点消息说话人 user_id（P7 按人加权心情用）。"""
    if chat_type != "private" and random.random() > MOOD_JUDGE_SAMPLE_RATE:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    asyncio.create_task(judge_mood_after_reply(anchor_text, response_texts, chat_type, speaker_uid=speaker_uid))
