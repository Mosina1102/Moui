# 定义了来自外部世界的信息
# 外部世界可以是某个聊天 不同平台的聊天 也可以是任意媒体
from datetime import datetime
from src.plugins.models.utils_model import LLMRequest
from src.config.config import global_config
from src.common.logger_manager import get_logger
import traceback
from src.plugins.utils.chat_message_builder import (
    get_raw_msg_before_timestamp_with_chat,
    build_readable_messages,
    get_raw_msg_by_timestamp_with_chat,
    num_new_messages_since,
    get_person_id_list,
)

logger = get_logger("observation")


# 所有观察的基类
class Observation:
    def __init__(self, observe_type, observe_id):
        self.observe_info = ""
        self.observe_type = observe_type
        self.observe_id = observe_id
        self.last_observe_time = datetime.now().timestamp()  # 初始化为当前时间

    async def observe(self):
        pass


# 聊天观察
class ChattingObservation(Observation):
    def __init__(self, chat_id):
        super().__init__("chat", chat_id)
        self.chat_id = chat_id

        self.talking_message = []
        self.talking_message_str = ""
        self.talking_message_str_truncate = ""

        self.name = global_config.BOT_NICKNAME
        self.nick_name = global_config.BOT_ALIAS_NAMES

        self.max_now_obs_len = global_config.observation_context_size
        self.overlap_len = global_config.compressed_length
        self.mid_memorys = []
        self.max_mid_memory_len = global_config.compress_length_limit
        self.mid_memory_info = ""

        self.person_list = []

        self.llm_summary = LLMRequest(
            model=global_config.llm_observation, temperature=0.7, max_tokens=300, request_type="chat_observation"
        )

    async def initialize(self):
        initial_messages = get_raw_msg_before_timestamp_with_chat(self.chat_id, self.last_observe_time, 20)
        self.talking_message = initial_messages  # 将这些消息设为初始上下文
        self.talking_message_str = await build_readable_messages(self.talking_message)

    # 进行一次观察 返回观察结果observe_info
    def get_observe_info(self, ids=None):
        if ids:
            mid_memory_str = ""
            for id in ids:
                print(f"id：{id}")
                try:
                    for mid_memory in self.mid_memorys:
                        if mid_memory["id"] == id:
                            mid_memory_by_id = mid_memory
                            msg_str = ""
                            for msg in mid_memory_by_id["messages"]:
                                msg_str += f"{msg['detailed_plain_text']}"
                            # time_diff = int((datetime.now().timestamp() - mid_memory_by_id["created_at"]) / 60)
                            # mid_memory_str += f"距离现在{time_diff}分钟前：\n{msg_str}\n"
                            mid_memory_str += f"{msg_str}\n"
                except Exception as e:
                    logger.error(f"获取mid_memory_id失败: {e}")
                    traceback.print_exc()
                    return self.talking_message_str

            return mid_memory_str + "现在群里正在聊：\n" + self.talking_message_str

        else:
            return self.talking_message_str

    async def observe(self):
        # 自上一次观察的新消息
        new_messages_list = get_raw_msg_by_timestamp_with_chat(
            chat_id=self.chat_id,
            timestamp_start=self.last_observe_time,
            timestamp_end=datetime.now().timestamp(),
            limit=self.max_now_obs_len,
            limit_mode="latest",
        )

        last_obs_time_mark = self.last_observe_time
        if new_messages_list:
            self.last_observe_time = new_messages_list[-1]["time"]
            self.talking_message.extend(new_messages_list)

        if len(self.talking_message) > self.max_now_obs_len:
            # 计算需要移除的消息数量，保留最新的 max_now_obs_len 条
            messages_to_remove_count = len(self.talking_message) - self.max_now_obs_len
            oldest_messages = self.talking_message[:messages_to_remove_count]
            self.talking_message = self.talking_message[messages_to_remove_count:]  # 保留后半部分，即最新的

            oldest_messages_str = await build_readable_messages(
                messages=oldest_messages, timestamp_mode="normal", read_mark=0
            )

            # 调用 LLM 总结主题
            prompt = (
                f"请总结以下聊天记录的主题：\n{oldest_messages_str}\n"
                f"详细且准确的用一句话概括包括人物事件和主要信息和场面氛围和人物情绪，不要分点。"
                f"如果其中有未完成的话题、约定、争论的分歧点或重要的细节（比如谁说了什么关键的话），务必一并写进去："
            )
            summary = "没有主题的闲聊"  # 默认值
            try:
                summary_result, _ = await self.llm_summary.generate_response_async(prompt)
                if summary_result:  # 确保结果不为空
                    summary = summary_result
            except Exception as e:
                logger.error(f"总结主题失败 for chat {self.chat_id}: {e}")
                # 保留默认总结 "没有主题的闲聊"

            mid_memory = {
                "id": str(int(datetime.now().timestamp())),
                "theme": summary,
                "messages": oldest_messages,  # 存储原始消息对象
                "readable_messages": oldest_messages_str,
                # "timestamps": oldest_timestamps,
                "chat_id": self.chat_id,
                "created_at": datetime.now().timestamp(),
            }

            self.mid_memorys.append(mid_memory)
            if len(self.mid_memorys) > self.max_mid_memory_len:
                self.mid_memorys.pop(0)  # 移除最旧的

            mid_memory_str = "之前聊天的内容概述是：\n"
            for mid_memory_item in self.mid_memorys:  # 重命名循环变量以示区分
                time_diff = int((datetime.now().timestamp() - mid_memory_item["created_at"]) / 60)
                mid_memory_str += (
                    f"距离现在{time_diff}分钟前(聊天记录id:{mid_memory_item['id']})：{mid_memory_item['theme']}\n"
                )
            self.mid_memory_info = mid_memory_str

        # 精准引用：统一大脑看的这份记录开编号（行首 #n），同时冻结编号→真实消息映射供 [引用#n] 解析
        self.talking_message_str = await build_readable_messages(
            messages=self.talking_message,
            timestamp_mode="lite",
            read_mark=last_obs_time_mark,
            numbered=True,
            chat_id=self.chat_id,
        )
        self.talking_message_str_truncate = await build_readable_messages(
            messages=self.talking_message,
            timestamp_mode="normal",
            read_mark=last_obs_time_mark,
            truncate=True,
        )

        self.person_list = await get_person_id_list(self.talking_message)

        # print(f"self.11111person_list: {self.person_list}")

        logger.trace(
            f"Chat {self.chat_id} - 压缩早期记忆：{self.mid_memory_info}\n现在聊天内容：{self.talking_message_str}"
        )

    async def has_new_messages_since(self, timestamp: float) -> bool:
        """检查指定时间戳之后是否有新消息"""
        count = num_new_messages_since(chat_id=self.chat_id, timestamp_start=timestamp)
        return count > 0
