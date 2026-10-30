"""方案K层4：交接备忘录（一次性语义，v2.2）。

memo 描述"一次中断"，生命周期三保险（v2.2 新坑D/E/G）：
1. 她重新发言成功 → heartFC 发送成功路径调 discard_memo 立即作废
2. 注入前新鲜度校验：last_bot_ts > memo.created → 她已重新开口 → 删除不注入
3. _generate finally 强制清 generating 状态（防任务异常后永久卡死）

素材模块内自取 DB（坑①：heartFC 的 _cycle_history 是内存态，中断即空，不能当数据源）。
后台预生成（坑⑯）：触发判定满足 → create_task，当轮 fail-open 返回空，下一轮注入。
不设 rounds_left 扣减（坑⑮根治：no_reply 轮不消耗任何东西）。
"""
import asyncio
import threading
import time
from typing import Dict, List, Tuple

from src.common.logger_manager import get_logger
from src.config.config import global_config

logger = get_logger("handoff_memo")

_memos: Dict[str, Dict] = {}  # {stream_id: {"text", "created", "status": "ready"|"generating"}}
_failed_until: Dict[str, float] = {}  # 生成失败退避：stream_id -> 可重试时间戳
_lock = threading.Lock()

_INJECT_TMPL = (
    "\n【刚才的你】\n{text}\n"
    "（这是上下文重置前你的状态，接话时先接上这个；话题要是已经凉了就大方起新话头）\n"
)
_FAIL_BACKOFF_SECONDS = 600  # 生成失败后 10 分钟内不重试（防失败风暴）


def _get_bot_recent(stream_id: str, limit: int = 15) -> Tuple[float, List[str]]:
    """DB 自取她最近的发言（坑①：DB 是唯一事实源）。返回 (她最后一条发言的ts, 她最近发言文本列表)。"""
    try:
        from src.plugins.utils.chat_message_builder import get_raw_msg_before_timestamp_with_chat

        bot_qq = str(getattr(global_config, "BOT_QQ", "") or "")
        msgs = get_raw_msg_before_timestamp_with_chat(chat_id=stream_id, timestamp=time.time(), limit=limit)
        contents: List[str] = []
        last_ts = 0.0
        for m in msgs:
            user_info = m.get("user_info") or {}
            if str(user_info.get("user_id") or "") == bot_qq and bot_qq:
                ts = float(m.get("time", 0) or 0)
                if ts > last_ts:
                    last_ts = ts
                txt = str(m.get("processed_plain_text", "") or "").strip()
                if txt:
                    contents.append(txt)
        return last_ts, contents[:3]
    except Exception as e:
        logger.error(f"DB 取她的发言失败: {e}")
        return 0.0, []


async def get_memo_block(stream_id: str, chat_stream) -> str:
    """统一大脑每轮回调用（由 unified_brain 注入区 await）。

    执行顺序：过期清理 → 取 last_bot_ts → 新鲜度校验 → 触发判定+后台预生成 → 返回注入块。
    全程 try/except（铁律3 fail-open），异常一律返回 ""。
    """
    if not stream_id:
        return ""
    try:
        min_gap = float(getattr(global_config, "plastic_handoff_min_gap_min", 5) or 5)
        max_gap = float(getattr(global_config, "plastic_handoff_max_gap_min", 30) or 30)
        now = time.time()

        # 1) 过期清理
        with _lock:
            memo = _memos.get(stream_id)
            if memo and now - float(memo.get("created", 0) or 0) > max_gap * 60:
                _memos.pop(stream_id, None)
                memo = None
            generating = bool(memo and memo.get("status") == "generating")
            ready_memo = memo if (memo and memo.get("status") == "ready") else None

        # 2) DB 自取她的最近发言（坑①）
        last_bot_ts, bot_msgs = _get_bot_recent(stream_id, limit=15)
        gap_min = (now - last_bot_ts) / 60.0 if last_bot_ts > 0 else None

        # 3) 新鲜度校验（新坑D）：她已重新发过言 → 该次中断语境终结 → 删除不注入
        if ready_memo and last_bot_ts > float(ready_memo.get("created", 0) or 0):
            with _lock:
                _memos.pop(stream_id, None)
            return ""

        # 5) ready → 注入（只读不扣减，坑⑮）
        if ready_memo:
            return _INJECT_TMPL.format(text=ready_memo.get("text", ""))

        # 4) 触发判定 + 后台预生成（坑⑯）
        if generating:
            return ""
        if now < _failed_until.get(stream_id, 0):
            return ""  # 失败退避期内
        if gap_min is not None and min_gap <= gap_min <= max_gap and bot_msgs:
            with _lock:
                cur = _memos.get(stream_id)
                if cur:
                    return ""  # 已有任务占位
                _memos[stream_id] = {"status": "generating", "created": now}
            asyncio.create_task(_generate(stream_id, gap_min, bot_msgs, gen_start=now))
        return ""
    except Exception as e:
        logger.error(f"get_memo_block 异常: {e}")
        return ""


async def _generate(stream_id: str, gap_min: float, bot_msgs: List[str], gen_start: float) -> None:
    """后台生成 memo（坑⑯）。finally 强制清 generating 状态（新坑E）。"""
    try:
        from src.plugins.models.utils_model import LLMRequest
        from src.plugins.utils.chat_message_builder import (
            build_readable_messages,
            get_raw_msg_before_timestamp_with_chat,
        )

        msgs = get_raw_msg_before_timestamp_with_chat(chat_id=stream_id, timestamp=time.time(), limit=5)
        if msgs:
            new_moves = await build_readable_messages(
                msgs, replace_bot_name=True, merge_messages=True, timestamp_mode="normal", read_mark=0.0
            )
        else:
            new_moves = "（无）"

        bot_name = getattr(global_config, "BOT_NICKNAME", "") or "Moui"
        bot_lines = "\n".join(f"- {t[:50]}" for t in bot_msgs) or "（无）"
        prompt = (
            f"QQ机器人\"{bot_name}\"刚才在群里连续说话后中断了{gap_min:.0f}分钟，现在群里又有动静，她可能要重新发言。\n"
            f"她中断前的发言（从近到远）：\n{bot_lines}\n"
            f"群里的新动静：\n{new_moves[:1500]}\n"
            f"帮她写一份接话备忘（≤100字，第三人称\"她\"），四件事缺一不可："
            f"①她刚才在做什么/聊什么话题 ②在跟谁聊 ③话题进行到哪一步 ④她当时的心情。\n"
            f"只输出备忘文本，不要任何前缀。中断已经{gap_min:.0f}分钟了，"
            f"如果判断话题大概率已经翻篇，就如实写\"话题应该已经凉了，可以起新话头\"。"
        )

        llm = LLMRequest(
            model=global_config.llm_topic_judge,
            request_type="handoff_memo",
            temperature=0.3,
            max_tokens=150,
        )
        response, _, _ = await llm.generate_response(prompt)
        text = (response or "").strip()[:200]
        if not text:
            logger.info("生成结果为空，放弃")
            with _lock:
                _failed_until[stream_id] = time.time() + _FAIL_BACKOFF_SECONDS
            return

        # 写前复核（新坑D）：生成期间她已发言 → 该次中断语境已终结 → 丢弃
        last_bot_ts, _ = _get_bot_recent(stream_id, limit=5)
        with _lock:
            if last_bot_ts > gen_start + 1:  # +1s 容忍时钟精度
                _memos.pop(stream_id, None)
                logger.info("生成期间她已重新发言，memo 丢弃")
                return
            _memos[stream_id] = {"text": text, "created": time.time(), "status": "ready"}
        logger.info(f"memo 就绪: {text[:40]}")
    except Exception as e:
        logger.error(f"生成失败: {e}")
        with _lock:
            _failed_until[stream_id] = time.time() + _FAIL_BACKOFF_SECONDS
    finally:
        # 新坑E：无论成败必须清 generating 状态（防任务异常后永久卡死）
        with _lock:
            cur = _memos.get(stream_id)
            if cur and cur.get("status") == "generating":
                _memos.pop(stream_id, None)


def discard_memo(stream_id: str) -> None:
    """她的回复发送成功后由 heartFC 发送成功路径调用（新坑D/G）：
    中断语境已终结，memo 立即作废（对 generating 状态同样立即删除）。"""
    try:
        with _lock:
            _memos.pop(stream_id, None)
    except Exception:
        pass


def snapshot() -> Dict[str, Dict]:
    """只读快照（监控看板用），不动内部状态。"""
    with _lock:
        return {sid: dict(m) for sid, m in _memos.items()}


def force_memo(stream_id: str, text: str) -> None:
    """外部强制落一份 ready memo（方案K层5 teardown 用，绕过间隔判定与新鲜度校验）。
    注意：这份 memo 仍受一次性语义约束——她下次发言成功即被 discard。"""
    try:
        text = (text or "").strip()
        if not text:
            return
        with _lock:
            _memos[stream_id] = {"text": text[:200], "created": time.time(), "status": "ready"}
        logger.info(f"外部强制落 memo: {text[:40]}")
    except Exception as e:
        logger.error(f"force_memo 异常: {e}")
