import asyncio
import time
from typing import Optional, List
from src.common.logger import get_module_logger
from ..chat.chat_stream import ChatStream
from ..chat.message import Message
from maim_message import UserInfo, Seg
from ..chat.utils import process_llm_response
from src.plugins.chat.message import MessageSending
from ..message.api import global_api
from ..storage.storage import MessageStorage
from ...config.config import global_config


logger = get_module_logger("message_sender")


class DirectMessageSender:
    """直接消息发送器"""

    def __init__(self, private_name: str):
        self.private_name = private_name
        self.storage = MessageStorage()

    async def send_message(
        self,
        chat_stream: ChatStream,
        content: str,
        reply_to_message: Optional[Message] = None,
    ) -> None:
        """发送消息到聊天流（分段发送，无延迟）

        Args:
            chat_stream: 聊天流
            content: 消息内容
            reply_to_message: 要回复的消息（可选）
        """
        try:
            # 处理消息内容，分段
            processed_messages: List[str] = process_llm_response(content, chat_stream.stream_id)
            
            if not processed_messages:
                logger.warning(f"[私聊][{self.private_name}]没有可发送的消息")
                return

            # 获取Moui的信息
            bot_user_info = UserInfo(
                user_id=global_config.BOT_QQ,
                user_nickname=global_config.BOT_NICKNAME,
                platform=chat_stream.platform,
            )

            thinking_start_time = time.time()

            # 逐条发送分段消息
            for i, msg_text in enumerate(processed_messages):
                if not msg_text.strip():
                    continue
                    
                # 为每条消息生成唯一ID
                message_id = f"dm{round(time.time() + i * 0.1, 2)}"

                message_segment = Seg(type="text", data=msg_text)
                
                # 构建消息对象
                message = MessageSending(
                    message_id=message_id,
                    chat_stream=chat_stream,
                    bot_user_info=bot_user_info,
                    sender_info=reply_to_message.message_info.user_info if reply_to_message else None,
                    message_segment=message_segment,
                    reply=reply_to_message if i == 0 else None,  # 只在第一条消息回复
                    is_head=(i == 0),  # 只有第一条是 head
                    is_emoji=False,
                    thinking_start_time=thinking_start_time,
                )

                # 处理消息
                await message.process()

                # 私聊引用回复：第一条消息带 reply 目标时生成原生引用段
                if i == 0 and reply_to_message is not None:
                    try:
                        message.set_reply()
                    except Exception as e:
                        logger.warning(f"[私聊][{self.private_name}]设置引用失败（不影响发送）: {e}")

                # 发送前增强：[引用自己]标记转换 + "@昵称"转真@（与群聊同款，异常不影响发送）
                try:
                    from ..chat.outbound_enhance import enhance_outgoing_message

                    await enhance_outgoing_message(message)
                except Exception as e:
                    logger.warning(f"[私聊][{self.private_name}]发送前增强失败（不影响发送）: {e}")

                # 打字模拟：每段各自计算打字时间（拟人——真人打完一段发一段）
                try:
                    from ..chat.utils import calculate_typing_time

                    typing_delay = calculate_typing_time(
                        input_string=msg_text,
                        thinking_start_time=thinking_start_time,
                        is_emoji=False,
                    )
                    if typing_delay > 0:
                        await asyncio.sleep(typing_delay)
                except Exception as e:
                    logger.warning(f"[私聊][{self.private_name}]打字模拟失败（不影响发送）: {e}")

                # 直接发送，不走 message_manager（避免额外调度延迟）
                await global_api.send_message(message)
                await self.storage.store_message(message, chat_stream)

                logger.info(f"[私聊][{self.private_name}]PFC消息片段{i+1}已发送: {msg_text[:30]}...")

            logger.info(f"[私聊][{self.private_name}]PFC消息已全部发送，共{len(processed_messages)}条")

            # 心情判断：代码侧强制触发（元工具靠模型自觉不可靠，实测V4.1从不主动调report_mood）
            # 异步spawn不阻塞发送；元工具report_mood保留作双保险
            try:
                anchor_text = ""
                if reply_to_message is not None:
                    anchor_text = getattr(reply_to_message, "processed_plain_text", "") or ""
                from ..moods.mood_llm_judge import spawn_mood_judge

                spawn_mood_judge(anchor_text, [m for m in processed_messages if m.strip()], chat_type="private")
            except Exception as e:
                logger.warning(f"[私聊][{self.private_name}]触发心情判断失败（不影响发送）: {e}")

        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]PFC消息发送失败: {str(e)}")
            raise
