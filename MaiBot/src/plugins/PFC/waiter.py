from src.common.logger import get_module_logger
from .chat_observer import ChatObserver
from .conversation_info import ConversationInfo

# from src.individuality.individuality import Individuality # 不再需要
from ...config.config import global_config
import time
import asyncio

logger = get_module_logger("waiter")

# --- 在这里设定你想要的超时时间（秒） ---
# 例如： 120 秒 = 2 分钟
DESIRED_TIMEOUT_SECONDS = 300
ADAPTIVE_MAX_SECONDS = 900  # 自适应窗口上限：15 分钟（分段慢打型选手）


class Waiter:
    """等待处理类"""

    def __init__(self, stream_id: str, private_name: str):
        self.chat_observer = ChatObserver.get_instance(stream_id, private_name)
        self.name = global_config.BOT_NICKNAME
        self.private_name = private_name
        self._unanswered_replan_budget = 2  # "发现插话→重新规划"的预算（防 planner 连续 wait 时无限循环烧LLM）
        # self.wait_accumulated_time = 0 # 不再需要累加计时

    def _has_unanswered_other_msg(self) -> bool:
        """检查最后一条消息是否为对方发送——对方可能在她生成回复期间插了话，这种消息不能无视"""
        try:
            msgs = self.chat_observer.get_cached_messages(limit=1)
            if not msgs:
                return False
            last = msgs[-1]
            uid = str(getattr(getattr(last, "user_info", None), "user_id", "") or "")
            return bool(uid) and uid != str(global_config.BOT_QQ)
        except Exception as e:
            logger.debug(f"[私聊][{self.private_name}]插话检测失败: {e}")
            return False

    def _adaptive_timeout(self) -> float:
        """按对方的打字节奏自适应等待窗口。

        有人喜欢断断续续分段打字（每条间隔几分钟）——检测到这种节奏时把窗口
        拉长到间隔的 1.5 倍（上限 900 秒），否则保持默认 300 秒。
        """
        try:
            msgs = self.chat_observer.get_cached_messages(limit=10)
            times = sorted(
                float(getattr(getattr(m, "message_info", None), "time", 0) or 0)
                for m in msgs
            )
            times = [t for t in times if t > 0]
            if len(times) < 3:
                return DESIRED_TIMEOUT_SECONDS
            gaps = [b - a for a, b in zip(times, times[1:]) if b - a > 0]
            max_gap = max(gaps) if gaps else 0
            if max_gap > 90:  # 出现过 90 秒以上的打字间隔 = 慢打节奏
                return min(ADAPTIVE_MAX_SECONDS, max(DESIRED_TIMEOUT_SECONDS, max_gap * 1.5))
        except Exception as e:
            logger.debug(f"[私聊][{self.private_name}]自适应窗口计算失败: {e}")
        return DESIRED_TIMEOUT_SECONDS

    async def wait(self, conversation_info: ConversationInfo) -> bool:
        """等待用户新消息或超时"""
        wait_start_time = time.time()
        # 插话检测：她生成回复期间对方发来的消息不算"新消息"（早于本等待起点），
        # 但绝不能无视——发现最后一条是对方的，立即重新规划去回应它
        if self._unanswered_replan_budget > 0 and self._has_unanswered_other_msg():
            self._unanswered_replan_budget -= 1
            logger.info(f"[私聊][{self.private_name}]发现对方在回复期间插了话，立即重新规划回应")
            return False
        timeout_seconds = self._adaptive_timeout()
        logger.info(f"[私聊][{self.private_name}]进入常规等待状态 (超时: {timeout_seconds:.0f} 秒)...")

        while True:
            # 检查是否有新消息
            if self.chat_observer.new_message_after(wait_start_time):
                logger.info(f"[私聊][{self.private_name}]等待结束，收到新消息")
                self._unanswered_replan_budget = 2  # 收到真新消息，恢复插话检测预算
                return False  # 返回 False 表示不是超时

            # 检查是否超时
            elapsed_time = time.time() - wait_start_time
            if elapsed_time > timeout_seconds:
                logger.info(f"[私聊][{self.private_name}]等待超过 {timeout_seconds:.0f} 秒...添加思考目标。")
                self._unanswered_replan_budget = 2
                wait_goal = {
                    "goal": f"你等待了{elapsed_time / 60:.1f}分钟，注意可能在对方看来聊天已经结束，思考接下来要做什么",
                    "reasoning": "对方很久没有回复你的消息了",
                }
                conversation_info.goal_list.append(wait_goal)
                logger.info(f"[私聊][{self.private_name}]添加目标: {wait_goal}")
                return True  # 返回 True 表示超时

            await asyncio.sleep(5)  # 每 5 秒检查一次
            logger.debug(
                f"[私聊][{self.private_name}]等待中..."
            )  # 可以考虑把这个频繁日志注释掉，只在超时或收到消息时输出

    async def wait_listening(self, conversation_info: ConversationInfo) -> bool:
        """倾听用户发言或超时"""
        wait_start_time = time.time()
        if self._unanswered_replan_budget > 0 and self._has_unanswered_other_msg():
            self._unanswered_replan_budget -= 1
            logger.info(f"[私聊][{self.private_name}]发现对方在回复期间插了话，立即重新规划回应")
            return False
        timeout_seconds = self._adaptive_timeout()
        logger.info(f"[私聊][{self.private_name}]进入倾听等待状态 (超时: {timeout_seconds:.0f} 秒)...")

        while True:
            # 检查是否有新消息
            if self.chat_observer.new_message_after(wait_start_time):
                logger.info(f"[私聊][{self.private_name}]倾听等待结束，收到新消息")
                self._unanswered_replan_budget = 2
                return False  # 返回 False 表示不是超时

            # 检查是否超时
            elapsed_time = time.time() - wait_start_time
            if elapsed_time > timeout_seconds:
                logger.info(f"[私聊][{self.private_name}]倾听等待超过 {timeout_seconds:.0f} 秒...添加思考目标。")
                self._unanswered_replan_budget = 2
                wait_goal = {
                    # 保持 goal 文本一致
                    "goal": f"你等待了{elapsed_time / 60:.1f}分钟，对方似乎话说一半突然消失了，可能忙去了？也可能忘记了回复？要问问吗？还是结束对话？或继续等待？思考接下来要做什么",
                    "reasoning": "对方话说一半消失了，很久没有回复",
                }
                conversation_info.goal_list.append(wait_goal)
                logger.info(f"[私聊][{self.private_name}]添加目标: {wait_goal}")
                return True  # 返回 True 表示超时

            await asyncio.sleep(5)  # 每 5 秒检查一次
            logger.debug(f"[私聊][{self.private_name}]倾听等待中...")  # 同上，可以考虑注释掉
