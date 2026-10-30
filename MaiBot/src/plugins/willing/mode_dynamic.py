import asyncio
import random
import time
from typing import Dict
from .willing_manager import BaseWillingManager


class DynamicWillingManager(BaseWillingManager):
    def __init__(self):
        super().__init__()
        self.chat_reply_willing: Dict[str, float] = {}  # 存储每个聊天流的回复意愿
        self.chat_high_willing_mode: Dict[str, bool] = {}  # 存储每个聊天流是否处于高回复意愿期
        self.chat_msg_count: Dict[str, int] = {}  # 存储每个聊天流接收到的消息数量
        self.chat_last_mode_change: Dict[str, float] = {}  # 存储每个聊天流上次模式切换的时间
        self.chat_high_willing_duration: Dict[str, int] = {}  # 高意愿期持续时间(秒)
        self.chat_low_willing_duration: Dict[str, int] = {}  # 低意愿期持续时间(秒)
        self.chat_last_reply_time: Dict[str, float] = {}  # 存储每个聊天流上次回复的时间
        self.chat_last_sender_id: Dict[str, str] = {}  # 存储每个聊天流上次回复的用户ID
        self.chat_conversation_context: Dict[str, bool] = {}  # 标记是否处于对话上下文中
        self._decay_task = None
        self._mode_switch_task = None
        self.time_window_conversation_reset = 300
        self.time_window_followup = 120
        self.msg_threshold_followup = 5

    async def async_task_starter(self):
        if self._decay_task is None:
            self._decay_task = asyncio.create_task(self._decay_reply_willing())
        if self._mode_switch_task is None:
            self._mode_switch_task = asyncio.create_task(self._mode_switch_check())

    async def _decay_reply_willing(self):
        """定期衰减回复意愿"""
        while True:
            await asyncio.sleep(5)
            for chat_id in self.chat_reply_willing:
                is_high_mode = self.chat_high_willing_mode.get(chat_id, False)
                if is_high_mode:
                    # 高回复意愿期内轻微衰减
                    self.chat_reply_willing[chat_id] = max(0.5, self.chat_reply_willing[chat_id] * 0.95)
                else:
                    # 低回复意愿期内正常衰减
                    self.chat_reply_willing[chat_id] = max(0, self.chat_reply_willing[chat_id] * 0.8)

    async def _mode_switch_check(self):
        """定期检查是否需要切换回复意愿模式"""
        while True:
            current_time = time.time()
            await asyncio.sleep(10)  # 每10秒检查一次

            for chat_id in self.chat_high_willing_mode:
                last_change_time = self.chat_last_mode_change.get(chat_id, 0)
                is_high_mode = self.chat_high_willing_mode.get(chat_id, False)

                # 获取当前模式的持续时间
                duration = 0
                if is_high_mode:
                    duration = self.chat_high_willing_duration.get(chat_id, 180)  # 默认3分钟
                else:
                    duration = self.chat_low_willing_duration.get(chat_id, random.randint(300, 1200))  # 默认5-20分钟

                # 检查是否需要切换模式
                if current_time - last_change_time > duration:
                    self._switch_willing_mode(chat_id)
                elif not is_high_mode and random.random() < 0.1:
                    # 低回复意愿期有10%概率随机切换到高回复期
                    self._switch_willing_mode(chat_id)

                # 检查对话上下文状态是否需要重置
                last_reply_time = self.chat_last_reply_time.get(chat_id, 0)
                if current_time - last_reply_time > self.time_window_conversation_reset:  # 5分钟无交互，重置对话上下文
                    self.chat_conversation_context[chat_id] = False

    def _switch_willing_mode(self, chat_id: str):
        """切换聊天流的回复意愿模式"""
        is_high_mode = self.chat_high_willing_mode.get(chat_id, False)

        if is_high_mode:
            # 从高回复期切换到低回复期
            self.chat_high_willing_mode[chat_id] = False
            self.chat_reply_willing[chat_id] = 0.1  # 设置为最低回复意愿
            self.chat_low_willing_duration[chat_id] = random.randint(600, 1200)  # 10-20分钟
            self.logger.debug(f"聊天流 {chat_id} 切换到低回复意愿期，持续 {self.chat_low_willing_duration[chat_id]} 秒")
        else:
            # 从低回复期切换到高回复期
            self.chat_high_willing_mode[chat_id] = True
            self.chat_reply_willing[chat_id] = 1.0  # 设置为较高回复意愿
            self.chat_high_willing_duration[chat_id] = random.randint(180, 240)  # 3-4分钟
            self.logger.debug(
                f"聊天流 {chat_id} 切换到高回复意愿期，持续 {self.chat_high_willing_duration[chat_id]} 秒"
            )

        self.chat_last_mode_change[chat_id] = time.time()
        self.chat_msg_count[chat_id] = 0  # 重置消息计数

    def _ensure_chat_initialized(self, chat_id: str):
        """确保聊天流的所有数据已初始化"""
        if chat_id not in self.chat_reply_willing:
            self.chat_reply_willing[chat_id] = 0.1

        if chat_id not in self.chat_high_willing_mode:
            self.chat_high_willing_mode[chat_id] = False
            self.chat_last_mode_change[chat_id] = time.time()
            self.chat_low_willing_duration[chat_id] = random.randint(300, 1200)  # 5-20分钟

        if chat_id not in self.chat_msg_count:
            self.chat_msg_count[chat_id] = 0

        if chat_id not in self.chat_conversation_context:
            self.chat_conversation_context[chat_id] = False

    async def get_reply_probability(self, message_id):
        """改变指定聊天流的回复意愿并返回回复概率"""
        # 获取或创建聊天流
        willing_info = self.ongoing_messages[message_id]
        stream = willing_info.chat
        chat_id = stream.stream_id
        sender_id = str(willing_info.message.message_info.user_info.user_id)
        current_time = time.time()

        self._ensure_chat_initialized(chat_id)

        # 增加消息计数
        self.chat_msg_count[chat_id] = self.chat_msg_count.get(chat_id, 0) + 1

        current_willing = self.chat_reply_willing.get(chat_id, 0)
        is_high_mode = self.chat_high_willing_mode.get(chat_id, False)
        msg_count = self.chat_msg_count.get(chat_id, 0)
        in_conversation_context = self.chat_conversation_context.get(chat_id, False)

        # 检查是否是对话上下文中的追问
        last_reply_time = self.chat_last_reply_time.get(chat_id, 0)
        last_sender = self.chat_last_sender_id.get(chat_id, "")

        # 如果是同一个人在短时间内（2分钟内）发送消息，且消息数量较少（<=5条），视为追问
        if sender_id and sender_id == last_sender and current_time - last_reply_time < self.time_window_followup and msg_count <= self.msg_threshold_followup:
            in_conversation_context = True
            self.chat_conversation_context[chat_id] = True
            self.logger.debug("检测到追问 (同一用户), 提高回复意愿")
            current_willing += 0.3

        # 特殊情况处理
        if willing_info.is_mentioned_bot:
            current_willing += 0.5
            in_conversation_context = True
            self.chat_conversation_context[chat_id] = True
            self.logger.debug(f"被提及, 当前意愿: {current_willing}")

        if willing_info.is_emoji:
            current_willing = self.global_config.emoji_response_penalty * 0.1
            self.logger.debug(f"表情包, 当前意愿: {current_willing}")

        # 根据话题兴趣度适当调整
        if willing_info.interested_rate > 0.5:
            current_willing += (
                (willing_info.interested_rate - 0.5) * 0.5 * self.global_config.response_interested_rate_amplifier
            )

        # 根据当前模式计算回复概率
        base_probability = 0.0

        if in_conversation_context:
            # 在对话上下文中，降低基础回复概率
            base_probability = 0.5 if is_high_mode else 0.25
            self.logger.debug(f"处于对话上下文中，基础回复概率: {base_probability}")
        elif is_high_mode:
            # 高回复周期：4-8句话有50%的概率会回复一次
            base_probability = 0.50 if 4 <= msg_count <= 8 else 0.2
        else:
            # 低回复周期：需要最少15句才有30%的概率会回一句
            base_probability = 0.30 if msg_count >= 15 else 0.03 * min(msg_count, 10)

        # 考虑回复意愿的影响
        reply_probability = base_probability * current_willing * self.global_config.response_willing_amplifier

        # 检查群组权限（如果是群聊）
        if willing_info.group_info:
            if willing_info.group_info.group_id in self.global_config.talk_frequency_down_groups:
                reply_probability = reply_probability / self.global_config.down_frequency_rate

        # 限制最大回复概率
        reply_probability = min(reply_probability, 0.75)  # 设置最大回复概率为75%
        if reply_probability < 0:
            reply_probability = 0

        # 记录当前发送者ID以便后续追踪
        if sender_id:
            self.chat_last_sender_id[chat_id] = sender_id

        self.chat_reply_willing[chat_id] = min(current_willing, 3.0)

        return reply_probability

    async def before_generate_reply_handle(self, message_id):
        """开始思考后降低聊天流的回复意愿"""
        stream = self.ongoing_messages[message_id].chat
        if stream:
            chat_id = stream.stream_id
            self._ensure_chat_initialized(chat_id)
            current_willing = self.chat_reply_willing.get(chat_id, 0)

            # 回复后减少回复意愿
            self.chat_reply_willing[chat_id] = max(0.0, current_willing - 0.3)

            # 标记为对话上下文中
            self.chat_conversation_context[chat_id] = True

            # 记录最后回复时间
            self.chat_last_reply_time[chat_id] = time.time()

            # 重置消息计数
            self.chat_msg_count[chat_id] = 0

    async def not_reply_handle(self, message_id):
        """决定不回复后提高聊天流的回复意愿"""
        stream = self.ongoing_messages[message_id].chat
        if stream:
            chat_id = stream.stream_id
            self._ensure_chat_initialized(chat_id)
            is_high_mode = self.chat_high_willing_mode.get(chat_id, False)
            current_willing = self.chat_reply_willing.get(chat_id, 0)
            in_conversation_context = self.chat_conversation_context.get(chat_id, False)

            # 根据当前模式调整不回复后的意愿增加
            if is_high_mode:
                willing_increase = 0.1
            elif in_conversation_context:
                # 在对话上下文中但决定不回复，小幅增加回复意愿
                willing_increase = 0.15
            else:
                willing_increase = random.uniform(0.05, 0.1)

            self.chat_reply_willing[chat_id] = min(2.0, current_willing + willing_increase)

    async def bombing_buffer_message_handle(self, message_id):
        return await super().bombing_buffer_message_handle(message_id)

    async def after_generate_reply_handle(self, message_id):
        return await super().after_generate_reply_handle(message_id)
