"""方案K：可塑性记忆定时调度器（层2群像审查 + 层3每日反思 + 层1滚动清理，二期挂层5 sweep）。

挂载点：background_tasks.BackgroundTaskManager（60s tick，照 proactive_manager 注入模式）。
全部配置读取走 getattr 防御（铁律4）；tick 内全组件 try/except（铁律3 fail-open）。
"""
import asyncio
import time

from src.common.logger_manager import get_logger
from src.config.config import global_config

logger = get_logger("plastic_scheduler")

_TICK_INTERVAL_SECONDS = 60
_REVIEWED_MAX = 200
# 429/网络抖动退避重试（老大拍板）：首次 1 分钟、翻倍封顶 5 分钟。
# 这些任务都是低频定时任务，重试成本≈0；退避期内跳过，窗口内下个 tick 自动补跑
_RETRY_BASE_SEC = 60.0
_RETRY_CAP_SEC = 300.0


class PlasticScheduler:
    """可塑性记忆定时调度：60s tick 一次，按小时窗口分发任务。"""

    def __init__(self):
        self._reviewed: dict = {}  # {stream_id: "MM-DD"} 今日已审（仅成功后标记）
        self._archived_today: set = set()  # [方案L] 今日已归档 stream_id（仅成功后标记）
        self._archived_running: set = set()  # [方案L] 归档任务进行中（防重复调度）
        self._review_fail: dict = {}  # {stream_id: (fail_count, next_retry_ts)}
        self._archive_fail: dict = {}  # {stream_id: (fail_count, next_retry_ts)}
        self._reflection_fail: tuple = (0, 0.0)  # (fail_count, next_retry_ts)
        self._episodic_cleanup_date: str = ""  # [方案L] 情节卡清理日标记
        self._last_cleanup_date: str = ""
        self._reflection_done_date: str = ""
        self._setup_done = False

    def _next_backoff(self, fail_count: int) -> float:
        """指数退避：60 → 120 → 240 → 300 → 300 ..."""
        return min(_RETRY_BASE_SEC * (2 ** fail_count), _RETRY_CAP_SEC)

    def setup(self) -> None:
        """幂等初始化。"""
        if self._setup_done:
            return
        self._setup_done = True
        enabled = getattr(global_config, "plastic_memory_enable", False)
        logger.info(f"调度器初始化 enable={enabled}")

    async def run_forever(self) -> None:
        """周期主循环。启动先歇 30s 避开启动风暴。"""
        await asyncio.sleep(30)
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                logger.info("调度任务已取消")
                break
            except Exception as e:
                logger.error(f"tick 异常: {e}")
            await asyncio.sleep(_TICK_INTERVAL_SECONDS)

    async def _tick(self) -> None:
        if not getattr(global_config, "plastic_memory_enable", False):
            return
        now = time.localtime()
        now_ts = time.time()
        today = time.strftime("%m-%d", now)
        hour = now.tm_hour
        minute = now.tm_min

        # ---- 层1 打分流滚动清理：每日一次 ----
        if self._last_cleanup_date != today:
            self._last_cleanup_date = today
            self._archived_today.clear()  # [方案L] 归档标记跨天清空（坑⑯）
            self._review_fail.clear()  # 失败计数不带到明天
            self._archive_fail.clear()
            self._archived_running.clear()
            self._reflection_fail = (0, 0.0)
            try:
                from .score_events import cleanup

                removed = cleanup(int(getattr(global_config, "plastic_score_retention_days", 30) or 30))
                if removed:
                    logger.info(f"打分流清理 {removed} 条")
            except Exception as e:
                logger.error(f"打分流清理失败: {e}")

        # ---- 层2 群像审查：审查小时窗口内，各群按 stream_id 哈希分钟错峰 ----
        if getattr(global_config, "plastic_group_review_enabled", False):
            review_hour = int(getattr(global_config, "plastic_group_review_hour", 4) or 4)
            if hour == review_hour:
                try:
                    from .group_reviewer import review_group
                    from .score_events import active_stream_ids

                    for sid in active_stream_ids(days=1):
                        if self._reviewed.get(sid) == today:
                            continue
                        # 429退避期内跳过，窗口内下个 tick 自动补跑
                        fails, next_ts = self._review_fail.get(sid, (0, 0.0))
                        if now_ts < next_ts:
                            continue
                        # 坑㉓：判定方向必须是"当前分钟 >= 哈希分钟"，漏审顺延次日无害
                        hash_minute = int(hash(str(sid))) % 60
                        if minute < hash_minute:
                            continue
                        group_name = ""
                        try:
                            from src.plugins.chat.chat_stream import chat_manager

                            group_name = chat_manager.get_stream_name(sid) or ""
                        except Exception:
                            pass
                        logger.info(f"开始群像审查: {group_name or sid}")
                        if await review_group(sid, group_name):
                            # 成功才标记；失败走退避重试（旧版先标记导致429当天不补跑，验尸修正）
                            self._reviewed[sid] = today
                            self._review_fail.pop(sid, None)
                        else:
                            self._review_fail[sid] = (fails + 1, now_ts + self._next_backoff(fails))
                            logger.warning(f"群像审查失败({group_name or sid})，{self._next_backoff(fails):.0f}s后重试")
                    # 防内存缓慢膨胀
                    if len(self._reviewed) > _REVIEWED_MAX:
                        self._reviewed = {k: v for k, v in self._reviewed.items() if v == today}
                except Exception as e:
                    logger.error(f"群像审查调度失败: {e}")

        # ---- [方案L] 人物卡&情节卡归档：archive_hour 窗口，当日活跃群哈希分钟错峰 ----
        if getattr(global_config, "person_cards_enable", False):
            archive_hour = int(getattr(global_config, "person_archive_hour", 3) or 3)
            if hour == archive_hour:
                try:
                    # 情节卡过期清理 + 脏行落盘：每日一次（方案4.2，随归档窗口执行）
                    if self._episodic_cleanup_date != today:
                        self._episodic_cleanup_date = today
                        try:
                            from .episodic_cards import cleanup_expired

                            removed_ep = cleanup_expired(time.time())
                            if removed_ep:
                                logger.info(f"情节卡清理 {removed_ep} 条")
                        except Exception as e:
                            logger.error(f"情节卡清理失败: {e}")
                        # 世界卡清理（方案L二期B，同一日标记）
                        if getattr(global_config, "world_cards_enable", False):
                            try:
                                from .world_cards import cleanup_world_cards

                                removed_wc = cleanup_world_cards(time.time())
                                if removed_wc:
                                    logger.info(f"世界卡清理 {removed_wc} 条")
                            except Exception as e:
                                logger.error(f"世界卡清理失败: {e}")

                    from .memory_archiver import archive_group
                    from .score_events import active_stream_ids

                    for sid in active_stream_ids(days=1):
                        if sid in self._archived_today or sid in self._archived_running:
                            continue
                        # 429退避期内跳过，窗口内下个 tick 自动补跑
                        fails, next_ts = self._archive_fail.get(sid, (0, 0.0))
                        if now_ts < next_ts:
                            continue
                        # 坑㉓同款判定方向：当前分钟 >= 哈希分钟，漏的顺延窗口内下一 tick 无害
                        hash_minute = int(hash(str(sid))) % 60
                        if minute < hash_minute:
                            continue
                        import asyncio as _asyncio

                        self._archived_running.add(sid)  # 占位防重复调度，成败由回调落账
                        _task = _asyncio.create_task(self._safe_archive(sid))

                        def _archive_done(t, _sid: str = sid):
                            try:
                                if t.result():
                                    self._archived_today.add(_sid)
                                    self._archive_fail.pop(_sid, None)
                                else:
                                    fails, _ = self._archive_fail.get(_sid, (0, 0.0))
                                    self._archive_fail[_sid] = (fails + 1, time.time() + self._next_backoff(fails))
                                    logger.warning(
                                        f"记忆归档失败({_sid[:8]})，{self._next_backoff(fails):.0f}s后重试"
                                    )
                            except Exception as cb_e:
                                logger.error(f"归档回调异常: {cb_e}")
                            finally:
                                self._archived_running.discard(_sid)

                        _task.add_done_callback(_archive_done)
                except Exception as e:
                    logger.error(f"记忆归档调度失败: {e}")

        # ---- 层3 每日反思：反思小时触发一次，失败按 429 退避重试 ----
        if getattr(global_config, "plastic_reflection_enabled", False):
            reflect_hour = int(getattr(global_config, "plastic_reflection_hour", 5) or 5)
            if hour == reflect_hour and self._reflection_done_date != today:
                fails, next_ts = self._reflection_fail
                if now_ts >= next_ts:
                    result = None
                    try:
                        from .reflection import run_daily_reflection

                        result = await run_daily_reflection()
                    except Exception as e:
                        logger.error(f"每日反思异常: {e}")
                    skipped = str((result or {}).get("skipped") or "")
                    if result is not None and not skipped:
                        logger.info(f"每日反思完成: {result}")
                        self._reflection_done_date = today
                        self._reflection_fail = (0, 0.0)
                    elif skipped.startswith("events<"):
                        # 素材不足：重试无意义，标记今日已处理
                        logger.info(f"每日反思跳过（{skipped}）")
                        self._reflection_done_date = today
                    else:
                        # 解析失败/网络异常（多半429）：退避后窗口内重试
                        self._reflection_fail = (fails + 1, now_ts + self._next_backoff(fails))
                        logger.warning(f"每日反思失败({skipped[:40] or '无输出'})，{self._next_backoff(fails):.0f}s后重试")

        # ---- （层5）突发会话清扫：冷场拆除 + 强制交接 ----
        if getattr(global_config, "enable_burst_session", False):
            try:
                from ..heartFC_chat.session_pool import session_pool

                session_pool.sweep()
            except Exception as e:
                logger.error(f"突发会话清扫失败: {e}")

    async def _safe_archive(self, sid: str) -> bool:
        """[方案L] 单群归档的安全包装。返回 True=完成/正常跳过，False=可重试失败（供退避重试）。"""
        try:
            from .memory_archiver import archive_group

            return await archive_group(sid)
        except Exception as e:
            logger.error(f"记忆归档异常: {e}")
            return False


plastic_scheduler = PlasticScheduler()
