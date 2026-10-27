# src/plugins/heartFC_chat/heartFC_sender.py
import asyncio  # 重新导入 asyncio
from typing import Dict, Optional  # 重新导入类型
from ..message.api import global_api
from ..chat.message import MessageSending, MessageThinking  # 只保留 MessageSending 和 MessageThinking
from ..storage.storage import MessageStorage
from ..chat.utils import truncate_message
from src.common.logger_manager import get_logger
from src.plugins.chat.utils import calculate_typing_time


logger = get_logger("sender")


class HeartFCSender:
    """管理消息的注册、即时处理、发送和存储，并跟踪思考状态。"""

    def __init__(self):
        self.storage = MessageStorage()
        # 用于存储活跃的思考消息
        self.thinking_messages: Dict[str, Dict[str, MessageThinking]] = {}
        self._thinking_lock = asyncio.Lock()  # 保护 thinking_messages 的锁
        # 机器人消息临时池：{chat_id: [最近发送的消息内容列表]}
        self.bot_message_pool: Dict[str, list] = {}
        self._pool_lock = asyncio.Lock()  # 保护 bot_message_pool 的锁
        self.MAX_POOL_SIZE = 5  # 每个聊天最多保存最近5条

    async def send_message(self, message: MessageSending) -> None:
        """合并后的消息发送函数，包含WS发送和日志记录"""
        message_preview = truncate_message(message.processed_plain_text)

        try:
            # 发送前增强：[引用]/[引用自己]标记转真引用框（PFC/普通聊天链路都有，这里补齐 HeartFC 链路）
            try:
                from ..chat.outbound_enhance import enhance_outgoing_message

                await enhance_outgoing_message(message)
            except Exception as enhance_e:
                logger.error(f"发送前消息增强失败（不影响发送）: {enhance_e}")

            # 直接调用API发送消息
            await global_api.send_message(message)
            logger.success(f"发送消息   '{message_preview}'   成功")

        except Exception as e:
            logger.error(f"发送消息   '{message_preview}'   失败: {str(e)}")
            if not message.message_info.platform:
                raise ValueError(f"未找到平台：{message.message_info.platform} 的url配置，请检查配置文件") from e
            raise e  # 重新抛出其他异常

    async def register_thinking(self, thinking_message: MessageThinking):
        """注册一个思考中的消息。"""
        if not thinking_message.chat_stream or not thinking_message.message_info.message_id:
            logger.error("无法注册缺少 chat_stream 或 message_id 的思考消息")
            return

        chat_id = thinking_message.chat_stream.stream_id
        message_id = thinking_message.message_info.message_id

        async with self._thinking_lock:
            if chat_id not in self.thinking_messages:
                self.thinking_messages[chat_id] = {}
            if message_id in self.thinking_messages[chat_id]:
                logger.warning(f"[{chat_id}] 尝试注册已存在的思考消息 ID: {message_id}")
            self.thinking_messages[chat_id][message_id] = thinking_message
            logger.debug(f"[{chat_id}] Registered thinking message: {message_id}")

    async def complete_thinking(self, chat_id: str, message_id: str):
        """完成并移除一个思考中的消息记录。"""
        async with self._thinking_lock:
            if chat_id in self.thinking_messages and message_id in self.thinking_messages[chat_id]:
                del self.thinking_messages[chat_id][message_id]
                logger.debug(f"[{chat_id}] Completed thinking message: {message_id}")
                if not self.thinking_messages[chat_id]:
                    del self.thinking_messages[chat_id]
                    logger.debug(f"[{chat_id}] Removed empty thinking message container.")

    def is_thinking(self, chat_id: str, message_id: str) -> bool:
        """检查指定的消息 ID 是否当前正处于思考状态。"""
        return chat_id in self.thinking_messages and message_id in self.thinking_messages[chat_id]

    async def add_bot_message(self, chat_id: str, content: str):
        """将机器人发送的消息添加到临时池"""
        if not content or not content.strip():
            return
        async with self._pool_lock:
            if chat_id not in self.bot_message_pool:
                self.bot_message_pool[chat_id] = []
            self.bot_message_pool[chat_id].append(content.strip())
            # 保持最近的消息数量
            if len(self.bot_message_pool[chat_id]) > self.MAX_POOL_SIZE:
                self.bot_message_pool[chat_id] = self.bot_message_pool[chat_id][-self.MAX_POOL_SIZE:]

    async def get_bot_messages(self, chat_id: str) -> list:
        """获取指定聊天的机器人消息历史"""
        async with self._pool_lock:
            return list(self.bot_message_pool.get(chat_id, []))

    async def get_thinking_start_time(self, chat_id: str, message_id: str) -> Optional[float]:
        """获取已注册思考消息的开始时间。"""
        async with self._thinking_lock:
            thinking_message = self.thinking_messages.get(chat_id, {}).get(message_id)
            return thinking_message.thinking_start_time if thinking_message else None

    async def type_and_send_message(self, message: MessageSending, type=False):
        """
        立即处理、发送并存储单个 MessageSending 消息。
        调用此方法前，应先调用 register_thinking 注册对应的思考消息。
        此方法执行后会调用 complete_thinking 清理思考状态。
        """
        if not message.chat_stream:
            logger.error("消息缺少 chat_stream，无法发送")
            return
        if not message.message_info or not message.message_info.message_id:
            logger.error("消息缺少 message_info 或 message_id，无法发送")
            return

        chat_id = message.chat_stream.stream_id
        message_id = message.message_info.message_id

        try:
            _ = message.update_thinking_time()

            # --- 条件应用 set_reply 逻辑 ---
            if message.apply_set_reply_logic and message.is_head and not message.is_private_message():
                logger.debug(f"[{chat_id}] 应用 set_reply 逻辑: {message.processed_plain_text[:20]}...")
                message.set_reply()
            # --- 结束条件 set_reply ---

            await message.process()

            if type:
                typing_time = calculate_typing_time(
                    input_string=message.processed_plain_text,
                    thinking_start_time=message.thinking_start_time,
                    is_emoji=message.is_emoji,
                )
                await asyncio.sleep(typing_time)

            await self.send_message(message)
            await self.storage.store_message(message, message.chat_stream)
            # 记录到机器人消息临时池
            await self.add_bot_message(chat_id, message.processed_plain_text)

        except Exception as e:
            logger.error(f"[{chat_id}] 处理或存储消息 {message_id} 时出错: {e}")
            raise e
        finally:
            await self.complete_thinking(chat_id, message_id)

    async def send_and_store(self, message: MessageSending):
        """处理、发送并存储单个消息，不涉及思考状态管理。"""
        if not message.chat_stream:
            logger.error(f"[{message.message_info.platform or 'UnknownPlatform'}] 消息缺少 chat_stream，无法发送")
            return
        if not message.message_info or not message.message_info.message_id:
            logger.error(
                f"[{message.chat_stream.stream_id if message.chat_stream else 'UnknownStream'}] 消息缺少 message_info 或 message_id，无法发送"
            )
            return

        chat_id = message.chat_stream.stream_id
        message_id = message.message_info.message_id  # 获取消息ID用于日志

        try:
            await message.process()

            await asyncio.sleep(0.5)

            await self.send_message(message)  # 使用现有的发送方法
            await self.storage.store_message(message, message.chat_stream)  # 使用现有的存储方法
            # 记录到机器人消息临时池
            await self.add_bot_message(chat_id, message.processed_plain_text)

        except Exception as e:
            logger.error(f"[{chat_id}] 处理或存储消息 {message_id} 时出错: {e}")
            # 重新抛出异常，让调用者知道失败了
            raise e
