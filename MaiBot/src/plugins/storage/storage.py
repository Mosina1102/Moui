import re
from typing import Union

from ...common.database import db
from ..chat.message import MessageSending, MessageRecv
from ..chat.chat_stream import ChatStream
from src.common.logger import get_module_logger

logger = get_module_logger("message_storage")


class MessageStorage:
    @staticmethod
    async def store_message(message: Union[MessageSending, MessageRecv], chat_stream: ChatStream) -> None:
        """存储消息到数据库"""
        try:
            # 莫越权 救世啊
            pattern = r"<MainRule>.*?</MainRule>|<schedule>.*?</schedule>"

            processed_plain_text = message.processed_plain_text
            if processed_plain_text:
                filtered_processed_plain_text = re.sub(pattern, "", processed_plain_text, flags=re.DOTALL)
            else:
                filtered_processed_plain_text = ""

            detailed_plain_text = message.detailed_plain_text
            if detailed_plain_text:
                filtered_detailed_plain_text = re.sub(pattern, "", detailed_plain_text, flags=re.DOTALL)
            else:
                filtered_detailed_plain_text = ""

            message_data = {
                "message_id": message.message_info.message_id,
                "time": message.message_info.time,
                "chat_id": chat_stream.stream_id,
                "chat_info": chat_stream.to_dict(),
                "user_info": message.message_info.user_info.to_dict(),
                # 使用过滤后的文本
                "processed_plain_text": filtered_processed_plain_text,
                "detailed_plain_text": filtered_detailed_plain_text,
                "memorized_times": message.memorized_times,
                # 添加群昵称信息
                "group_nickname": message.message_info.user_info.user_nickname if message.message_info.user_info else "",
            }
            db.messages.insert_one(message_data)
        except Exception:
            logger.exception("存储消息失败")

    @staticmethod
    async def store_recalled_message(message_id: str, time: str, chat_stream: ChatStream) -> None:
        """存储撤回消息到数据库"""
        if "recalled_messages" not in db.list_collection_names():
            db.create_collection("recalled_messages")
        else:
            try:
                message_data = {
                    "message_id": message_id,
                    "time": time,
                    "stream_id": chat_stream.stream_id,
                }
                db.recalled_messages.insert_one(message_data)
            except Exception:
                logger.exception("存储撤回消息失败")

    @staticmethod
    async def remove_recalled_message(time: str) -> None:
        """删除撤回消息"""
        try:
            db.recalled_messages.delete_many({"time": {"$lt": time - 300}})
        except Exception:
            logger.exception("删除撤回消息失败")


# 如果需要其他存储相关的函数，可以在这里添加
