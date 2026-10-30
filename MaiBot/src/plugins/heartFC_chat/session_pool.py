"""方案K层5（二期实验）：长短模式2.0 —— 热聊突发会话池。

设计（v2.2 施工方案第七节）：
- 热聊时用原生 user/assistant 对白对做历史前缀：她的话在上下文里是"我自己说的"（assistant 角色），
  不是聊天记录文本——角色身份差异就是拟真增益来源；整段历史都是稳定前缀，热聊期缓存全命中反而更便宜。
- 冷聊（burst_idle_min 分钟无互动）→ sweep 拆除：压缩软着陆（坑㉑，悬崖变斜坡）→ 强制落层4交接 memo → 清空。
- 只对 burst_whitelist 白名单群生效（坑⑲：多群并发会缓存驱逐，A/B 只试最热 1-2 群）。
- delta 口径（坑⑳）：与 observation 同源的 readable 文本；assistant 存发送层最终文本（含 [引用] 标记）。
- 会话内存态，重启丢失可接受。
"""
import asyncio
import threading
import time
from typing import Dict, List, Optional

from src.common.logger_manager import get_logger
from src.config.config import global_config

logger = get_logger("session_pool")

_lock = threading.Lock()

_COMPACT_HEAD_PREFIX = "（以下是更早对话的压缩记录，不是任何人的新发言）"  # 新坑F：防角色语义污染


class BurstSession:
    __slots__ = ("stream_id", "messages", "est_tokens", "last_active", "compacting")

    def __init__(self, stream_id: str):
        self.stream_id = stream_id
        self.messages: List[Dict[str, str]] = []  # [{"role": "user"|"assistant", "content": str}] 追加式，禁止中间删除
        self.est_tokens = 0  # 粗估：中文 2 字符/token
        self.last_active = time.time()
        self.compacting = False


class SessionPool:
    def __init__(self):
        self._pool: Dict[str, BurstSession] = {}

    # ---------- 开关 ----------
    def enabled_for(self, group_id) -> bool:
        """总开关 + 白名单判定。group_id 为群 QQ 号。"""
        if not getattr(global_config, "enable_burst_session", False):
            return False
        try:
            whitelist = getattr(global_config, "burst_whitelist", []) or []
            return int(group_id) in [int(x) for x in whitelist]
        except (TypeError, ValueError):
            return False

    # ---------- 查询 ----------
    def has(self, stream_id: str) -> bool:
        with _lock:
            return stream_id in self._pool

    def get(self, stream_id: str) -> Optional[BurstSession]:
        with _lock:
            return self._pool.get(stream_id)

    # ---------- 生命周期 ----------
    def start_or_extend(self, stream_id: str, delta_user_text: str) -> Optional[BurstSession]:
        """无会话则新建（首条 user=delta，冷启动为观察快照）；有会话则追加一条 user。
        返回最新会话（可能为 None，若 delta 为空且无会话）。"""
        try:
            if not delta_user_text or not delta_user_text.strip():
                return self.get(stream_id)
            with _lock:
                session = self._pool.get(stream_id)
                if session is None:
                    session = BurstSession(stream_id)
                    self._pool[stream_id] = session
                session.messages.append({"role": "user", "content": delta_user_text})
                session.est_tokens += max(1, len(delta_user_text) // 2)
                session.last_active = time.time()
            self._maybe_schedule_compact(stream_id)
            return self.get(stream_id)
        except Exception as e:
            logger.error(f"start_or_extend 异常: {e}")
            return None

    def append_assistant(self, stream_id: str, text: str) -> None:
        """她的回复发送成功后调用（heartFC 发送成功路径）。text=发送层最终文本（保真口径）。"""
        try:
            if not text or not text.strip():
                return
            with _lock:
                session = self._pool.get(stream_id)
                if session is None:
                    return
                session.messages.append({"role": "assistant", "content": text})
                session.est_tokens += max(1, len(text) // 2)
                session.last_active = time.time()
            self._maybe_schedule_compact(stream_id)
        except Exception as e:
            logger.error(f"append_assistant 异常: {e}")

    # ---------- 超限软着陆（坑㉑）----------
    def _maybe_schedule_compact(self, stream_id: str) -> None:
        try:
            with _lock:
                session = self._pool.get(stream_id)
                if session is None or session.compacting:
                    return
                max_tokens = int(getattr(global_config, "burst_max_tokens", 8000) or 8000)
                if session.est_tokens <= max_tokens:
                    return
                session.compacting = True  # 占位防重复调度
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                with _lock:
                    session = self._pool.get(stream_id)
                    if session:
                        session.compacting = False
                return
            asyncio.create_task(self._compact_async(stream_id))
        except Exception as e:
            logger.error(f"压缩调度失败: {e}")
            with _lock:
                session = self._pool.get(stream_id)
                if session:
                    session.compacting = False

    async def _compact_async(self, stream_id: str) -> None:
        """把最旧约一半对白（成对切）压缩成接续块放回头部——不滑窗、不硬切。"""
        half = 0
        rest: List[Dict[str, str]] = []
        try:
            with _lock:
                session = self._pool.get(stream_id)
                if session is None or len(session.messages) < 4:
                    if session:
                        session.compacting = False
                    return
                half = len(session.messages) // 2
                if half % 2 == 1:
                    half += 1  # 成对切，保持后续 user/assistant 交替完整
                old_msgs = session.messages[:half]
                rest = session.messages[half:]
            dialog_text = "\n".join(
                f"{'她' if m['role'] == 'assistant' else '群友'}: {m['content'][:80]}" for m in old_msgs
            )

            from src.plugins.models.utils_model import LLMRequest

            llm = LLMRequest(
                model=global_config.llm_topic_judge,
                request_type="burst_compact",
                temperature=0.3,
                max_tokens=250,
            )
            prompt = (
                "把下面这段QQ群对话压缩成 ≤200 字的接续摘要（\"她\"指机器人Moui自己）。"
                "必须保留：聊过的话题、谁和她在聊、进行到哪一步、她的态度。只输出摘要文本。\n"
                f"{dialog_text[:3000]}"
            )
            response, _, _ = await llm.generate_response(prompt)
            summary = (response or "").strip()[:300]
            if not summary:
                summary = "（更早的对话因会话过长被略去）"

            with _lock:
                session = self._pool.get(stream_id)
                if session is not None:
                    head = {"role": "user", "content": _COMPACT_HEAD_PREFIX + summary}
                    session.messages = [head] + rest
                    session.est_tokens = sum(max(1, len(m["content"]) // 2) for m in session.messages)
                    session.compacting = False
            logger.info(f"压缩完成: {stream_id} 旧{half}条→接续块")
        except Exception as e:
            logger.error(f"压缩失败: {e}")
            with _lock:
                session = self._pool.get(stream_id)
                if session:
                    session.compacting = False

    # ---------- 拆除 ----------
    def teardown(self, stream_id: str, reason: str) -> None:
        """拆除：快照尾部 → 强制落层4交接 memo（绕过间隔判定）→ 清空。"""
        try:
            with _lock:
                session = self._pool.pop(stream_id, None)
            if session is None:
                return
            logger.info(f"拆除 {stream_id}: {reason}（历史 {len(session.messages)} 条）")
            self._force_handoff(stream_id, session)
        except Exception as e:
            logger.error(f"拆除异常: {e}")

    def _force_handoff(self, stream_id: str, session: BurstSession) -> None:
        """拆除时把尾部上下文写成层4 memo（不调 LLM，直接用对白快照拼，同步安全）。"""
        try:
            tail = session.messages[-4:]
            if not tail:
                return
            lines = []
            for m in tail:
                role = "她" if m["role"] == "assistant" else "群友"
                lines.append(f"{role}: {m['content'][:60]}")
            memo_text = "刚在热聊中，会话因长度被系统归档，凭下面的内容继续接：\n" + "\n".join(lines)
            from .handoff_memo import force_memo

            force_memo(stream_id, memo_text)
        except Exception as e:
            logger.error(f"强制交接失败: {e}")

    def sweep(self) -> None:
        """清扫冷会话（last_active 距今 > burst_idle_min）。由 plastic_scheduler.tick 调用。"""
        try:
            idle_min = int(getattr(global_config, "burst_idle_min", 10) or 10)
            deadline = time.time() - idle_min * 60
            expired: List[str] = []
            with _lock:
                for sid, session in list(self._pool.items()):
                    if session.last_active < deadline:
                        expired.append(sid)
            for sid in expired:
                self.teardown(sid, f"冷场>{idle_min}分钟")
        except Exception as e:
            logger.error(f"sweep 异常: {e}")

    def snapshot(self) -> List[Dict]:
        """只读快照（监控看板用）。"""
        with _lock:
            return [
                {
                    "stream_id": s.stream_id,
                    "turns": len(s.messages),
                    "est_tokens": s.est_tokens,
                    "last_active": s.last_active,
                    "compacting": s.compacting,
                    "tail": (s.messages[-1]["content"][:60] if s.messages else ""),
                }
                for s in self._pool.values()
            ]


def collect_delta_text(observation, since_ts: float) -> str:
    """从 observation 提取 since_ts 之后的群消息合并文本（坑⑳：与观察同源口径）。

    关于她自己的消息（状态翻转边界，v2.2 终验修正）：不过滤。
    - 专注模式回复：append_assistant 以 assistant 角色追加，且 last_active 去重（消息 time < 追加时刻
      的 last_active，不会重复进 delta）；
    - 普通模式回复（专注→普通→专注翻转窗内）：不经 _send_unified_reply，若过滤会从突发历史缺失
      → 以"名字: 文本"命名行进历史，与轮窗模式呈现她说的话同款方式，基线一致无新增污染。
    """
    try:
        msgs = getattr(observation, "talking_message", None) or []
        parts: List[str] = []
        for m in msgs:
            try:
                if float(m.get("time", 0) or 0) <= since_ts:
                    continue
                txt = str(m.get("processed_plain_text") or m.get("message") or "").strip()
                if not txt:
                    continue
                user_info = m.get("user_info") or {}
                name = user_info.get("user_nickname") or user_info.get("user_id") or "?"
                parts.append(f"{name}: {txt[:100]}")
            except Exception:
                continue
        return "\n".join(parts)
    except Exception as e:
        logger.error(f"提取增量失败: {e}")
        return ""


session_pool = SessionPool()
