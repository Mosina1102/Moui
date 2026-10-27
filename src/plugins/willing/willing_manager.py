from src.common.logger import LogConfig, WILLING_STYLE_CONFIG, LoguruLogger, get_module_logger
from dataclasses import dataclass
from ...config.config import global_config, BotConfig
from ..chat.chat_stream import ChatStream, GroupInfo
from ..chat.message import MessageRecv
from ..person_info.person_info import person_info_manager, PersonInfoManager
from abc import ABC, abstractmethod
import importlib
from typing import Dict, Optional
import asyncio

"""
基类方法概览：
以下8个方法是你必须在子类重写的（哪怕什么都不干）：
async_task_starter 在程序启动时执行，在其中用asyncio.create_task启动你想要执行的异步任务
before_generate_reply_handle 确定要回复后，在生成回复前的处理
after_generate_reply_handle 确定要回复后，在生成回复后的处理
not_reply_handle 确定不回复后的处理
get_reply_probability 获取回复概率
bombing_buffer_message_handle 缓冲器炸飞消息后的处理
get_variable_parameters 暂不确定
set_variable_parameters 暂不确定
以下2个方法根据你的实现可以做调整：
get_willing 获取某聊天流意愿
set_willing 设置某聊天流意愿
规范说明：
模块文件命名: `mode_{manager_type}.py` 
示例: 若 `manager_type="aggressive"`，则模块文件应为 `mode_aggressive.py`
类命名: `{manager_type}WillingManager` (首字母大写)
示例: 在 `mode_aggressive.py` 中，类名应为 `AggressiveWillingManager`
"""

willing_config = LogConfig(
    # 使用消息发送专用样式
    console_format=WILLING_STYLE_CONFIG["console_format"],
    file_format=WILLING_STYLE_CONFIG["file_format"],
)
logger = get_module_logger("willing", config=willing_config)


@dataclass
class WillingInfo:
    """此类保存意愿模块常用的参数

    Attributes:
        message (MessageRecv): 原始消息对象
        chat (ChatStream): 聊天流对象
        person_info_manager (PersonInfoManager): 用户信息管理对象
        chat_id (str): 当前聊天流的标识符
        person_id (str): 发送者的个人信息的标识符
        group_id (str): 群组ID（如果是私聊则为空）
        is_mentioned_bot (bool): 是否提及了bot
        is_emoji (bool): 是否为表情包
        interested_rate (float): 兴趣度
    """

    message: MessageRecv
    chat: ChatStream
    person_info_manager: PersonInfoManager
    chat_id: str
    person_id: str
    group_info: Optional[GroupInfo]
    is_mentioned_bot: bool
    is_emoji: bool
    interested_rate: float
    # current_mood: float  当前心情？


class BaseWillingManager(ABC):
    """回复意愿管理基类"""

    @classmethod
    def create(cls, manager_type: str) -> "BaseWillingManager":
        try:
            module = importlib.import_module(f".mode_{manager_type}", __package__)
            manager_class = getattr(module, f"{manager_type.capitalize()}WillingManager")
            if not issubclass(manager_class, cls):
                raise TypeError(f"Manager class {manager_class.__name__} is not a subclass of {cls.__name__}")
            else:
                logger.info(f"普通回复模式：{manager_type}")
            return manager_class()
        except (ImportError, AttributeError, TypeError) as e:
            module = importlib.import_module(".mode_classical", __package__)
            manager_class = module.ClassicalWillingManager
            logger.info(f"载入当前意愿模式{manager_type}失败，使用经典配方~~~~")
            logger.debug(f"加载willing模式{manager_type}失败，原因: {str(e)}。")
            return manager_class()

    def __init__(self):
        self.chat_reply_willing: Dict[str, float] = {}  # 存储每个聊天流的回复意愿(chat_id)
        self.ongoing_messages: Dict[str, WillingInfo] = {}  # 当前正在进行的消息(message_id)
        self.lock = asyncio.Lock()
        self.global_config: BotConfig = global_config
        self.logger: LoguruLogger = logger

    def setup(self, message: MessageRecv, chat: ChatStream, is_mentioned_bot: bool, interested_rate: float):
        person_id = person_info_manager.get_person_id(chat.platform, chat.user_info.user_id)
        self.ongoing_messages[message.message_info.message_id] = WillingInfo(
            message=message,
            chat=chat,
            person_info_manager=person_info_manager,
            chat_id=chat.stream_id,
            person_id=person_id,
            group_info=chat.group_info,
            is_mentioned_bot=is_mentioned_bot,
            is_emoji=message.is_emoji,
            interested_rate=interested_rate,
        )

    def delete(self, message_id: str):
        del_message = self.ongoing_messages.pop(message_id, None)
        if not del_message:
            logger.debug(f"尝试删除不存在的消息 ID: {message_id}，可能已被其他流程处理，喵~")

    @abstractmethod
    async def async_task_starter(self) -> None:
        """抽象方法：异步任务启动器"""
        pass

    @abstractmethod
    async def before_generate_reply_handle(self, message_id: str):
        """抽象方法：回复前处理"""
        pass

    @abstractmethod
    async def after_generate_reply_handle(self, message_id: str):
        """抽象方法：回复后处理"""
        pass

    @abstractmethod
    async def not_reply_handle(self, message_id: str):
        """抽象方法：不回复处理"""
        pass

    @abstractmethod
    async def get_reply_probability(self, message_id: str):
        """抽象方法：获取回复概率"""
        raise NotImplementedError

    @abstractmethod
    async def bombing_buffer_message_handle(self, message_id: str):
        """抽象方法：炸飞消息处理"""
        pass

    async def get_willing(self, chat_id: str):
        """获取指定聊天流的回复意愿"""
        async with self.lock:
            return self.chat_reply_willing.get(chat_id, 0)

    async def set_willing(self, chat_id: str, willing: float):
        """设置指定聊天流的回复意愿"""
        async with self.lock:
            self.chat_reply_willing[chat_id] = willing

    # @abstractmethod
    # async def get_variable_parameters(self) -> Dict[str, str]:
    #     """抽象方法：获取可变参数"""
    #     pass

    # @abstractmethod
    # async def set_variable_parameters(self, parameters: Dict[str, any]):
    #     """抽象方法：设置可变参数"""
    #     pass


def init_willing_manager() -> BaseWillingManager:
    """
    根据配置初始化并返回对应的WillingManager实例

    Returns:
        对应mode的WillingManager实例
    """
    mode = global_config.willing_mode.lower()
    return BaseWillingManager.create(mode)


# 全局willing_manager对象
willing_manager = init_willing_manager()
