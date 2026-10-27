import asyncio
from .willing_manager import BaseWillingManager


class ClassicalWillingManager(BaseWillingManager):
    def __init__(self):
        super().__init__()
        self._decay_task: asyncio.Task = None

    async def _decay_reply_willing(self):
        """定期衰减回复意愿"""
        while True:
            await asyncio.sleep(1)
            for chat_id in self.chat_reply_willing:
                self.chat_reply_willing[chat_id] = max(0, self.chat_reply_willing[chat_id] * 0.9)

    async def async_task_starter(self):
        if self._decay_task is None:
            self._decay_task = asyncio.create_task(self._decay_reply_willing())

    async def get_reply_probability(self, message_id):
        willing_info = self.ongoing_messages[message_id]
        chat_id = willing_info.chat_id
        current_willing = self.chat_reply_willing.get(chat_id, 0)

        interested_rate = willing_info.interested_rate * self.global_config.response_interested_rate_amplifier

        if interested_rate > 0.4:
            current_willing += interested_rate - 0.3

        if willing_info.is_mentioned_bot and current_willing < 1.0:
            current_willing += 1
        elif willing_info.is_mentioned_bot:
            current_willing += 0.05

        is_emoji_not_reply = False
        if willing_info.is_emoji:
            if self.global_config.emoji_response_penalty != 0:
                current_willing *= self.global_config.emoji_response_penalty
            else:
                is_emoji_not_reply = True

        self.chat_reply_willing[chat_id] = min(current_willing, 3.0)

        reply_probability = min(
            max((current_willing - 0.5), 0.01) * self.global_config.response_willing_amplifier * 2, 1
        )

        # 检查群组权限（如果是群聊）
        if (
            willing_info.group_info
            and willing_info.group_info.group_id in self.global_config.talk_frequency_down_groups
        ):
            reply_probability = reply_probability / self.global_config.down_frequency_rate

        if is_emoji_not_reply:
            reply_probability = 0

        return reply_probability

    async def before_generate_reply_handle(self, message_id):
        chat_id = self.ongoing_messages[message_id].chat_id
        current_willing = self.chat_reply_willing.get(chat_id, 0)
        self.chat_reply_willing[chat_id] = max(0, current_willing - 1.8)

    async def after_generate_reply_handle(self, message_id):
        if message_id not in self.ongoing_messages:
            return

        chat_id = self.ongoing_messages[message_id].chat_id
        current_willing = self.chat_reply_willing.get(chat_id, 0)
        if current_willing < 1:
            self.chat_reply_willing[chat_id] = min(1, current_willing + 0.4)

    async def bombing_buffer_message_handle(self, message_id):
        return await super().bombing_buffer_message_handle(message_id)

    async def not_reply_handle(self, message_id):
        return await super().not_reply_handle(message_id)
