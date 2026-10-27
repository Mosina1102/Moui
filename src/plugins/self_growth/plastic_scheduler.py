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


class PlasticScheduler:
    """可塑性记忆定时调度：60s tick 一次，按小时窗口分发任务。"""

    def __init__(self):
        self._reviewed: dict = {}  # {stream_id: "MM-DD"} 今日已审
        self._archived_today: set = set()  # [方案L] 今日已归档 stream_id
        self._episodic_cleanup_date: str = ""  # [方案L] 情节卡清理日标记
        self._last_cleanup_date: str = ""
        self._reflection_done_date: str = ""
        self._setup_done = False

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
        today = time.strftime("%m-%d", now)
        hour = now.tm_hour
        minute = now.tm_min

        # ---- 层1 打分流滚动清理：每日一次 ----
        if self._last_cleanup_date != today:
            self._last_cleanup_date = today
            self._archived_today.clear()  # [方案L] 归档标记跨天清空（坑⑯）
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
                        # 坑㉓：判定方向必须是"当前分钟 >= 哈希分钟"，漏审顺延次日无害
                        hash_minute = int(hash(str(sid))) % 60
                        if minute < hash_minute:
                            continue
                        self._reviewed[sid] = today
                        group_name = ""
                        try:
                            from src.plugins.chat.chat_stream import chat_manager

                            group_name = chat_manager.get_stream_name(sid) or ""
                        except Exception:
                            pass
                        logger.info(f"开始群像审查: {group_name or sid}")
                        await review_group(sid, group_name)
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
                        if sid in self._archived_today:
                            continue
                        # 坑㉓同款判定方向：当前分钟 >= 哈希分钟，漏的顺延窗口内下一 tick 无害
                        hash_minute = int(hash(str(sid))) % 60
                        if minute < hash_minute:
                            continue
                        self._archived_today.add(sid)
                        import asyncio as _asyncio

                        _asyncio.create_task(self._safe_archive(sid))
                except Exception as e:
                    logger.error(f"记忆归档调度失败: {e}")

        # ---- 层3 每日反思：反思小时触发一次 ----
        if getattr(global_config, "plastic_reflection_enabled", False):
            reflect_hour = int(getattr(global_config, "plastic_reflection_hour", 5) or 5)
            if hour == reflect_hour and self._reflection_done_date != today:
                self._reflection_done_date = today  # 先标记防重复触发（异常次日再试）
                try:
                    from .reflection import run_daily_reflection

                    result = await run_daily_reflection()
                    logger.info(f"每日反思完成: {result}")
                except Exception as e:
                    logger.error(f"每日反思失败: {e}")

        # ---- （层5）突发会话清扫：冷场拆除 + 强制交接 ----
        if getattr(global_config, "enable_burst_session", False):
            try:
                from ..heartFC_chat.session_pool import session_pool

                session_pool.sweep()
            except Exception as e:
                logger.error(f"突发会话清扫失败: {e}")

    async def _safe_archive(self, sid: str) -> None:
        """[方案L] 单群归档的安全包装（fail-open，异常只记日志）。"""
        try:
            from .memory_archiver import archive_group

            await archive_group(sid)
        except Exception as e:
            logger.error(f"记忆归档异常: {e}")


plastic_scheduler = PlasticScheduler()
