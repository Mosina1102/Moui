"""
llmcheck 模式：
此模式的一些参数不会在配置文件中显示，要修改请在可变参数下修改
此模式的特点：
1.在群聊内的连续对话场景下，使用大语言模型来判断回复概率
2.非连续对话场景,使用mxp模式的意愿管理器(可另外配置)
3.默认配置的是model_v3,当前参数适用于deepseek-v3-0324

继承自其他模式,实质上仅重写get_reply_probability方法,未来可能重构成一个插件,可方便地组装到其他意愿模式上。
目前的使用方式是拓展到其他意愿管理模式

"""

import time
from loguru import logger
from ..models.utils_model import LLMRequest
from ...config.config import global_config

# from ..chat.chat_stream import ChatStream
from ..chat.utils import get_recent_group_detailed_plain_text

# from .willing_manager import BaseWillingManager
from .mode_mxp import MxpWillingManager
import re
from functools import wraps


def is_continuous_chat(self, message_id: str):
    # 判断是否是连续对话，出于成本考虑，默认限制5条
    willing_info = self.ongoing_messages[message_id]
    chat_id = willing_info.chat_id
    group_info = willing_info.group_info
    config = self.global_config
    length = 5
    if chat_id:
        chat_talking_text = get_recent_group_detailed_plain_text(chat_id, limit=length, combine=True)
        if group_info:
            if str(config.BOT_QQ) in chat_talking_text:
                return True
            else:
                return False
    return False


def llmcheck_decorator(trigger_condition_func):
    def decorator(func):
        @wraps(func)
        def wrapper(self, message_id: str):
            if trigger_condition_func(self, message_id):
                # 满足条件，走llm流程
                return self.get_llmreply_probability(message_id)
            else:
                # 不满足条件，走默认流程
                return func(self, message_id)

        return wrapper

    return decorator


class LlmcheckWillingManager(MxpWillingManager):
    def __init__(self):
        super().__init__()
        self.model_v3 = LLMRequest(model=global_config.llm_normal, temperature=0.3)

    async def get_llmreply_probability(self, message_id: str):
        message_info = self.ongoing_messages[message_id]
        chat_id = message_info.chat_id
        config = self.global_config
        # 获取信息的长度
        length = 5
        if message_info.group_info and config:
            if message_info.group_info.group_id not in config.talk_allowed_groups:
                reply_probability = 0
                return reply_probability

        current_date = time.strftime("%Y-%m-%d", time.localtime())
        current_time = time.strftime("%H:%M:%S", time.localtime())
        chat_talking_prompt = ""
        if chat_id:
            chat_talking_prompt = get_recent_group_detailed_plain_text(chat_id, limit=length, combine=True)
        else:
            return 0

        # if is_mentioned_bot:
        #     return 1.0
        prompt = f"""
        假设你正在查看一个群聊，你在这个群聊里的网名叫{global_config.BOT_NICKNAME}，你还有很多别名: {"/".join(global_config.BOT_ALIAS_NAMES)}，
        现在群里聊天的内容是{chat_talking_prompt}，
        今天是{current_date}，现在是{current_time}。
        综合群内的氛围和你自己之前的发言，给出你认为**最新的消息**需要你回复的概率，数值在0到1之间。请注意，群聊内容杂乱，很多时候对话连续，但很可能不是在和你说话。
        如果最新的消息和你之前的发言在内容上连续，或者提到了你的名字或者称谓，将其视作明确指向你的互动，给出高于0.8的概率。如果现在是睡眠时间，直接概率为0。如果话题内容与你之前不是紧密相关，请不要给出高于0.1的概率。
        请注意是判断概率，而不是编写回复内容，
        仅输出在0到1区间内的概率值，不要给出你的判断依据。
        """

        content_check, reasoning_check, _ = await self.model_v3.generate_response(prompt)
        # logger.info(f"{prompt}")
        logger.info(f"{content_check} {reasoning_check}")
        probability = self.extract_marked_probability(content_check)
        # 兴趣系数修正 无关激活效率太高，暂时停用，待新记忆系统上线后调整
        probability += message_info.interested_rate * 0.25
        probability = min(1.0, probability)
        if probability <= 0.1:
            probability = min(0.03, probability)
        if probability >= 0.8:
            probability = max(probability, 0.90)

        # 当前表情包理解能力较差，少说就少错
        if message_info.is_emoji:
            probability *= global_config.emoji_response_penalty

        return probability

    @staticmethod
    def extract_marked_probability(text):
        """提取带标记的概率值 该方法主要用于测试微调prompt阶段"""
        text = text.strip()
        pattern = r"##PROBABILITY_START##(.*?)##PROBABILITY_END##"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            prob_str = match.group(1).strip()
            # 处理百分比（65% → 0.65）
            if "%" in prob_str:
                return float(prob_str.replace("%", "")) / 100
            # 处理分数（2/3 → 0.666...）
            elif "/" in prob_str:
                numerator, denominator = map(float, prob_str.split("/"))
                return numerator / denominator
            # 直接处理小数
            else:
                return float(prob_str)

        percent_match = re.search(r"(\d{1,3})%", text)  # 65%
        decimal_match = re.search(r"(0\.\d+|1\.0+)", text)  # 0.65
        fraction_match = re.search(r"(\d+)/(\d+)", text)  # 2/3
        try:
            if percent_match:
                prob = float(percent_match.group(1)) / 100
            elif decimal_match:
                prob = float(decimal_match.group(0))
            elif fraction_match:
                numerator, denominator = map(float, fraction_match.groups())
                prob = numerator / denominator
            else:
                return 0  # 无匹配格式

            # 验证范围是否合法
            if 0 <= prob <= 1:
                return prob
            return 0
        except (ValueError, ZeroDivisionError):
            return 0

    @llmcheck_decorator(is_continuous_chat)
    def get_reply_probability(self, message_id):
        return super().get_reply_probability(message_id)
