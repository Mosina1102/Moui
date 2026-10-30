from .observation import Observation, ChattingObservation
import asyncio
from src.config.config import global_config
import time
from typing import Optional, List, Dict, Tuple, Callable, Coroutine
import traceback
from src.common.logger_manager import get_logger
from src.plugins.chat.message import MessageRecv
from src.plugins.chat.chat_stream import chat_manager
import math
from src.plugins.heartFC_chat.heartFC_chat import HeartFChatting
from src.plugins.heartFC_chat.normal_chat import NormalChat
from src.heart_flow.mai_state_manager import MaiStateInfo
from src.heart_flow.chat_state_info import ChatState, ChatStateInfo
from src.heart_flow.sub_mind import SubMind


# 定义常量 (从 interest.py 移动过来)
MAX_INTEREST = 15.0

logger = get_logger("sub_heartflow")

PROBABILITY_INCREASE_RATE_PER_SECOND = 0.1
PROBABILITY_DECREASE_RATE_PER_SECOND = 0.1
MAX_REPLY_PROBABILITY = 1


class InterestChatting:
    def __init__(
        self,
        decay_rate=global_config.default_decay_rate_per_second,
        max_interest=MAX_INTEREST,
        trigger_threshold=global_config.reply_trigger_threshold,
        max_probability=MAX_REPLY_PROBABILITY,
    ):
        # 基础属性初始化
        self.interest_level: float = 0.0
        self.decay_rate_per_second: float = decay_rate
        self.max_interest: float = max_interest

        self.trigger_threshold: float = trigger_threshold
        self.max_reply_probability: float = max_probability
        self.is_above_threshold: bool = False

        # 任务相关属性初始化
        self.update_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._task_lock = asyncio.Lock()
        self._is_running = False

        self.interest_dict: Dict[str, tuple[MessageRecv, float, bool]] = {}
        self.update_interval = 1.0

        self.above_threshold = False
        self.start_hfc_probability = 0.0

    async def initialize(self):
        async with self._task_lock:
            if self._is_running:
                logger.debug("后台兴趣更新任务已在运行中。")
                return

            # 清理已完成或已取消的任务
            if self.update_task and (self.update_task.done() or self.update_task.cancelled()):
                self.update_task = None

            if not self.update_task:
                self._stop_event.clear()
                self._is_running = True
                self.update_task = asyncio.create_task(self._run_update_loop(self.update_interval))
                logger.debug("后台兴趣更新任务已创建并启动。")

    def add_interest_dict(self, message: MessageRecv, interest_value: float, is_mentioned: bool):
        """添加消息到兴趣字典

        参数:
            message: 接收到的消息
            interest_value: 兴趣值
            is_mentioned: 是否被提及

        功能:
            1. 将消息添加到兴趣字典
            2. 更新最后交互时间
            3. 如果字典长度超过10，删除最旧的消息
        """
        # 添加新消息
        self.interest_dict[message.message_info.message_id] = (message, interest_value, is_mentioned)

        # 如果字典长度超过10，删除最旧的消息
        if len(self.interest_dict) > 10:
            oldest_key = next(iter(self.interest_dict))
            self.interest_dict.pop(oldest_key)

    async def _calculate_decay(self):
        """计算兴趣值的衰减

        参数:
            current_time: 当前时间戳

        处理逻辑:
        1. 计算时间差
        2. 处理各种异常情况(负值/零值)
        3. 正常计算衰减
        4. 更新最后更新时间
        """

        # 处理极小兴趣值情况
        if self.interest_level < 1e-9:
            self.interest_level = 0.0
            return

        # 异常情况处理
        if self.decay_rate_per_second <= 0:
            logger.warning(f"衰减率({self.decay_rate_per_second})无效，重置兴趣值为0")
            self.interest_level = 0.0
            return

        # 正常衰减计算
        # 异常值防护：上游（如海马体激活值）爆炸产生 nan/inf 时，别让兴趣值跟着报废
        if math.isnan(self.interest_level) or math.isinf(self.interest_level):
            logger.warning(f"兴趣值异常({self.interest_level})，重置为0")
            self.interest_level = 0.0
            return

        try:
            decay_factor = math.pow(self.decay_rate_per_second, self.update_interval)
            self.interest_level *= decay_factor
            # 封顶：衰减路径也不允许越过 max_interest（这是 increase 之外唯一改值的入口）
            self.interest_level = min(self.interest_level, self.max_interest)
        except ValueError as e:
            logger.error(
                f"衰减计算错误: {e} 参数: 衰减率={self.decay_rate_per_second} 时间差={self.update_interval} 当前兴趣={self.interest_level}"
            )
            self.interest_level = 0.0

    async def _update_reply_probability(self):
        self.above_threshold = self.interest_level >= self.trigger_threshold
        if self.above_threshold:
            self.start_hfc_probability += PROBABILITY_INCREASE_RATE_PER_SECOND
        else:
            if self.start_hfc_probability > 0:
                self.start_hfc_probability = max(0, self.start_hfc_probability - PROBABILITY_DECREASE_RATE_PER_SECOND)

    async def increase_interest(self, value: float):
        self.interest_level += value
        self.interest_level = min(self.interest_level, self.max_interest)

    async def decrease_interest(self, value: float):
        self.interest_level -= value
        self.interest_level = max(self.interest_level, 0.0)

    async def get_interest(self) -> float:
        # 兜底：异常值清零 + 永不超过 max_interest（主动搭话的兴趣门槛依赖此值可信）
        if math.isnan(self.interest_level) or math.isinf(self.interest_level):
            self.interest_level = 0.0
        return min(self.interest_level, self.max_interest)

    async def get_state(self) -> dict:
        interest = self.interest_level  # 直接使用属性值
        return {
            "interest_level": round(interest, 2),
            "start_hfc_probability": round(self.start_hfc_probability, 4),
            "above_threshold": self.above_threshold,
        }

    # --- 新增后台更新任务相关方法 ---
    async def _run_update_loop(self, update_interval: float = 1.0):
        """后台循环，定期更新兴趣和回复概率。"""
        try:
            while not self._stop_event.is_set():
                try:
                    if self.interest_level != 0:
                        await self._calculate_decay()

                    await self._update_reply_probability()

                    # 等待下一个周期或停止事件
                    await asyncio.wait_for(self._stop_event.wait(), timeout=update_interval)
                except asyncio.TimeoutError:
                    # 正常超时，继续循环
                    continue
                except Exception as e:
                    logger.error(f"InterestChatting 更新循环出错: {e}")
                    logger.error(traceback.format_exc())
                    # 防止错误导致CPU飙升，稍作等待
                    await asyncio.sleep(5)
        except asyncio.CancelledError:
            logger.info("InterestChatting 更新循环被取消。")
        finally:
            self._is_running = False
            logger.info("InterestChatting 更新循环已停止。")

    async def stop_updates(self):
        """停止后台更新任务，使用锁确保并发安全"""
        async with self._task_lock:
            if not self._is_running:
                logger.debug("后台兴趣更新任务未运行。")
                return

            logger.info("正在停止 InterestChatting 后台更新任务...")
            self._stop_event.set()

            if self.update_task and not self.update_task.done():
                try:
                    # 等待任务结束，设置超时
                    await asyncio.wait_for(self.update_task, timeout=5.0)
                    logger.info("InterestChatting 后台更新任务已成功停止。")
                except asyncio.TimeoutError:
                    logger.warning("停止 InterestChatting 后台任务超时，尝试取消...")
                    self.update_task.cancel()
                    try:
                        await self.update_task  # 等待取消完成
                    except asyncio.CancelledError:
                        logger.info("InterestChatting 后台更新任务已被取消。")
                except Exception as e:
                    logger.error(f"停止 InterestChatting 后台任务时发生异常: {e}")
                finally:
                    self.update_task = None
                    self._is_running = False

    # --- 结束 新增方法 ---


class SubHeartflow:
    def __init__(
        self,
        subheartflow_id,
        mai_states: MaiStateInfo,
        hfc_no_reply_callback: Callable[[], Coroutine[None, None, None]],
    ):
        """子心流初始化函数

        Args:
            subheartflow_id: 子心流唯一标识符
            mai_states: Moui状态信息实例
            hfc_no_reply_callback: HFChatting 连续不回复时触发的回调
        """
        # 基础属性，两个值是一样的
        self.subheartflow_id = subheartflow_id
        self.chat_id = subheartflow_id
        self.hfc_no_reply_callback = hfc_no_reply_callback

        # Moui的状态
        self.mai_states = mai_states

        # 这个聊天流的状态
        self.chat_state: ChatStateInfo = ChatStateInfo()
        self.chat_state_changed_time: float = time.time()
        self.chat_state_last_time: float = 0
        self.history_chat_state: List[Tuple[ChatState, float]] = []

        # 兴趣检测器
        self.interest_chatting: InterestChatting = InterestChatting()

        # 活动状态管理
        self.should_stop = False  # 停止标志
        self.task: Optional[asyncio.Task] = None  # 后台任务

        # 随便水群 normal_chat 和 认真水群 heartFC_chat 实例
        # CHAT模式激活 随便水群  FOCUS模式激活 认真水群
        self.heart_fc_instance: Optional[HeartFChatting] = None  # 该sub_heartflow的HeartFChatting实例
        self.normal_chat_instance: Optional[NormalChat] = None  # 该sub_heartflow的NormalChat实例

        # 观察，目前只有聊天观察，可以载入多个
        # 负责对处理过的消息进行观察
        self.observations: List[ChattingObservation] = []  # 观察列表
        # self.running_knowledges = []  # 运行中的知识，待完善

        # LLM模型配置，负责进行思考
        self.sub_mind = SubMind(
            subheartflow_id=self.subheartflow_id, chat_state=self.chat_state, observations=self.observations
        )

        # 日志前缀
        self.log_prefix = chat_manager.get_stream_name(self.subheartflow_id) or self.subheartflow_id

    async def initialize(self):
        """异步初始化方法，创建兴趣流"""
        await self.interest_chatting.initialize()
        logger.debug(f"{self.log_prefix} InterestChatting 实例已初始化。")

    def update_last_chat_state_time(self):
        self.chat_state_last_time = time.time() - self.chat_state_changed_time

    async def _stop_normal_chat(self):
        """
        停止 NormalChat 实例
        切出 CHAT 状态时使用
        """
        if self.normal_chat_instance:
            logger.info(f"{self.log_prefix} 离开CHAT模式，结束 随便水群")
            try:
                await self.normal_chat_instance.stop_chat()  # 调用 stop_chat
            except Exception as e:
                logger.error(f"{self.log_prefix} 停止 NormalChat 监控任务时出错: {e}")
                logger.error(traceback.format_exc())

    async def _start_normal_chat(self) -> bool:
        """
        启动 NormalChat 实例，
        进入 CHAT 状态时使用

        确保 HeartFChatting 已停止
        """
        await self._stop_heart_fc_chat()  # 确保 专注聊天已停止

        log_prefix = self.log_prefix
        try:
            # 获取聊天流并创建 NormalChat 实例
            chat_stream = chat_manager.get_stream(self.chat_id)
            self.normal_chat_instance = NormalChat(chat_stream=chat_stream, interest_dict=self.get_interest_dict())

            logger.info(f"{log_prefix} 开始普通聊天，随便水群...")
            await self.normal_chat_instance.start_chat()  # <--- 修正：调用 start_chat
            return True
        except Exception as e:
            logger.error(f"{log_prefix} 启动 NormalChat 时出错: {e}")
            logger.error(traceback.format_exc())
            self.normal_chat_instance = None  # 启动失败，清理实例
            return False

    async def _stop_heart_fc_chat(self):
        """停止并清理 HeartFChatting 实例"""
        if self.heart_fc_instance:
            logger.debug(f"{self.log_prefix} 结束专注聊天...")
            try:
                await self.heart_fc_instance.shutdown()
            except Exception as e:
                logger.error(f"{self.log_prefix} 关闭 HeartFChatting 实例时出错: {e}")
                logger.error(traceback.format_exc())
            finally:
                # 无论是否成功关闭，都清理引用
                self.heart_fc_instance = None

    async def _start_heart_fc_chat(self) -> bool:
        """启动 HeartFChatting 实例，确保 NormalChat 已停止"""
        await self._stop_normal_chat()  # 确保普通聊天监控已停止
        self.clear_interest_dict()  # 清理兴趣字典，准备专注聊天

        log_prefix = self.log_prefix
        # 如果实例已存在，检查其循环任务状态
        if self.heart_fc_instance:
            # 如果任务已完成或不存在，则尝试重新启动
            if self.heart_fc_instance._loop_task is None or self.heart_fc_instance._loop_task.done():
                logger.info(f"{log_prefix} HeartFChatting 实例存在但循环未运行，尝试启动...")
                try:
                    await self.heart_fc_instance.start()  # 启动循环
                    logger.info(f"{log_prefix} HeartFChatting 循环已启动。")
                    return True
                except Exception as e:
                    logger.error(f"{log_prefix} 尝试启动现有 HeartFChatting 循环时出错: {e}")
                    logger.error(traceback.format_exc())
                    return False  # 启动失败
            else:
                # 任务正在运行
                logger.debug(f"{log_prefix} HeartFChatting 已在运行中。")
                return True  # 已经在运行

        # 如果实例不存在，则创建并启动
        logger.info(f"{log_prefix} Moui准备开始专注聊天...")
        try:
            # 创建 HeartFChatting 实例，并传递 从构造函数传入的 回调函数
            self.heart_fc_instance = HeartFChatting(
                chat_id=self.subheartflow_id,
                sub_mind=self.sub_mind,
                observations=self.observations,  # 传递所有观察者
                on_consecutive_no_reply_callback=self.hfc_no_reply_callback,  # <-- Use stored callback
            )

            # 初始化并启动 HeartFChatting
            if await self.heart_fc_instance._initialize():
                await self.heart_fc_instance.start()
                logger.debug(f"{log_prefix} Moui已成功进入专注聊天模式 (新实例已启动)。")
                return True
            else:
                logger.error(f"{log_prefix} HeartFChatting 初始化失败，无法进入专注模式。")
                self.heart_fc_instance = None  # 初始化失败，清理实例
                return False
        except Exception as e:
            logger.error(f"{log_prefix} 创建或启动 HeartFChatting 实例时出错: {e}")
            logger.error(traceback.format_exc())
            self.heart_fc_instance = None  # 创建或初始化异常，清理实例
            return False

    async def change_chat_state(self, new_state: "ChatState"):
        """更新sub_heartflow的聊天状态，并管理 HeartFChatting 和 NormalChat 实例及任务"""
        current_state = self.chat_state.chat_status

        if current_state == new_state:
            return

        log_prefix = self.log_prefix
        state_changed = False  # 标记状态是否实际发生改变

        # --- 状态转换逻辑 ---
        if new_state == ChatState.CHAT:
            # 移除限额检查逻辑
            logger.debug(f"{log_prefix} 准备进入或保持 聊天 状态")
            if await self._start_normal_chat():
                # logger.info(f"{log_prefix} 成功进入或保持 NormalChat 状态。")
                state_changed = True
            else:
                logger.error(f"{log_prefix} 启动 NormalChat 失败，无法进入 CHAT 状态。")
                # 考虑是否需要回滚状态或采取其他措施
                return  # 启动失败，不改变状态

        elif new_state == ChatState.FOCUSED:
            # 移除限额检查逻辑
            logger.debug(f"{log_prefix} 准备进入或保持 专注聊天 状态")
            if await self._start_heart_fc_chat():
                logger.debug(f"{log_prefix} 成功进入或保持 HeartFChatting 状态。")
                state_changed = True
            else:
                logger.error(f"{log_prefix} 启动 HeartFChatting 失败，无法进入 FOCUSED 状态。")
                # 启动失败，状态回滚到之前的状态或ABSENT？这里保持不改变
                return  # 启动失败，不改变状态

        elif new_state == ChatState.ABSENT:
            logger.info(f"{log_prefix} 进入 ABSENT 状态，停止所有聊天活动...")
            await self._stop_normal_chat()
            await self._stop_heart_fc_chat()
            state_changed = True  # 总是可以成功转换到 ABSENT

        # --- 更新状态和最后活动时间 ---
        if state_changed:
            self.update_last_chat_state_time()
            self.history_chat_state.append((current_state, self.chat_state_last_time))

            logger.info(
                f"{log_prefix} Moui的聊天状态从 {current_state.value} （持续了 {int(self.chat_state_last_time)} 秒） 变更为 {new_state.value}"
            )

            self.chat_state.chat_status = new_state
            self.chat_state_last_time = 0
            self.chat_state_changed_time = time.time()
        else:
            # 如果因为某些原因（如启动失败）没有成功改变状态，记录一下
            logger.debug(
                f"{log_prefix} 尝试将状态从 {current_state.value} 变为 {new_state.value}，但未成功或未执行更改。"
            )

    async def subheartflow_start_working(self):
        """启动子心流的后台任务

        功能说明:
        - 负责子心流的主要后台循环
        - 每30秒检查一次停止标志
        """
        logger.trace(f"{self.log_prefix} 子心流开始工作...")

        while not self.should_stop:
            await asyncio.sleep(30)  # 30秒检查一次停止标志

        logger.info(f"{self.log_prefix} 子心流后台任务已停止。")

    def update_current_mind(self, response):
        self.sub_mind.update_current_mind(response)

    def add_observation(self, observation: Observation):
        for existing_obs in self.observations:
            if existing_obs.observe_id == observation.observe_id:
                return
        self.observations.append(observation)

    def remove_observation(self, observation: Observation):
        if observation in self.observations:
            self.observations.remove(observation)

    def get_all_observations(self) -> list[Observation]:
        return self.observations

    def clear_observations(self):
        self.observations.clear()

    def _get_primary_observation(self) -> Optional[ChattingObservation]:
        if self.observations and isinstance(self.observations[0], ChattingObservation):
            return self.observations[0]
        logger.warning(f"SubHeartflow {self.subheartflow_id} 没有找到有效的 ChattingObservation")
        return None

    async def get_interest_state(self) -> dict:
        return await self.interest_chatting.get_state()

    def get_normal_chat_last_speak_time(self) -> float:
        if self.normal_chat_instance:
            return self.normal_chat_instance.last_speak_time
        return 0

    def get_interest_dict(self) -> Dict[str, tuple[MessageRecv, float, bool]]:
        return self.interest_chatting.interest_dict

    def clear_interest_dict(self):
        self.interest_chatting.interest_dict.clear()

    async def get_full_state(self) -> dict:
        """获取子心流的完整状态，包括兴趣、思维和聊天状态。"""
        interest_state = await self.get_interest_state()
        return {
            "interest_state": interest_state,
            "current_mind": self.sub_mind.current_mind,
            "chat_state": self.chat_state.chat_status.value,
            "chat_state_changed_time": self.chat_state_changed_time,
        }

    async def shutdown(self):
        """安全地关闭子心流及其管理的任务"""
        if self.should_stop:
            logger.info(f"{self.log_prefix} 子心流已在关闭过程中。")
            return

        logger.info(f"{self.log_prefix} 开始关闭子心流...")
        self.should_stop = True  # 标记为停止，让后台任务退出

        # 使用新的停止方法
        await self._stop_normal_chat()
        await self._stop_heart_fc_chat()

        # 停止兴趣更新任务
        if self.interest_chatting:
            logger.info(f"{self.log_prefix} 停止兴趣系统后台任务...")
            await self.interest_chatting.stop_updates()

        # 取消可能存在的旧后台任务 (self.task)
        if self.task and not self.task.done():
            logger.debug(f"{self.log_prefix} 取消子心流主任务 (Shutdown)...")
            self.task.cancel()
            try:
                await asyncio.wait_for(self.task, timeout=1.0)  # 给点时间响应取消
            except asyncio.CancelledError:
                logger.debug(f"{self.log_prefix} 子心流主任务已取消 (Shutdown)。")
            except asyncio.TimeoutError:
                logger.warning(f"{self.log_prefix} 等待子心流主任务取消超时 (Shutdown)。")
            except Exception as e:
                logger.error(f"{self.log_prefix} 等待子心流主任务取消时发生错误 (Shutdown): {e}")

        self.task = None  # 清理任务引用
        self.chat_state.chat_status = ChatState.ABSENT  # 状态重置为不参与

        logger.info(f"{self.log_prefix} 子心流关闭完成。")
