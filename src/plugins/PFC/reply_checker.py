import json
from typing import Tuple, List, Dict, Any
from src.common.logger import get_module_logger
from ..models.utils_model import LLMRequest
from ...config.config import global_config
from .chat_observer import ChatObserver
from .pfc_utils import extract_first_json_span, loads_with_newline_repair
from maim_message import UserInfo

logger = get_module_logger("reply_checker")


class ReplyChecker:
    """回复检查器"""

    def __init__(self, stream_id: str, private_name: str):
        self.llm = LLMRequest(
            model=global_config.llm_PFC_reply_checker, temperature=0.50, max_tokens=600, request_type="reply_check"
        )
        self.name = global_config.BOT_NICKNAME
        self.private_name = private_name
        self.chat_observer = ChatObserver.get_instance(stream_id, private_name)
        self.max_retries = 3  # 最大重试次数

    async def check(
        self,
        reply: str,
        goal: str,
        chat_history: List[Dict[str, Any]],
        chat_history_text: str,
        retry_count: int = 0,
        tool_results_text: str = "",
    ) -> Tuple[bool, str, bool]:
        """检查生成的回复是否合适

        Args:
            reply: 生成的回复
            goal: 对话目标
            retry_count: 当前重试次数

        Returns:
            Tuple[bool, str, bool]: (是否合适, 原因, 是否需要重新规划)
        """
        # 不再从 observer 获取，直接使用传入的 chat_history
        # messages = self.chat_observer.get_cached_messages(limit=20)
        try:
            # 筛选出最近由 Bot 自己发送的消息
            bot_messages = []
            for msg in reversed(chat_history):
                user_info = UserInfo.from_dict(msg.get("user_info", {}))
                if str(user_info.user_id) == str(global_config.BOT_QQ):  # 确保比较的是字符串
                    bot_messages.append(msg.get("processed_plain_text", ""))
                if len(bot_messages) >= 2:  # 只和最近的两条比较
                    break
                # 进行比较
            if bot_messages:
                # 可以用简单比较，或者更复杂的相似度库 (如 difflib)
                # 简单比较：是否完全相同
                if reply == bot_messages[0]:  # 和最近一条完全一样
                    logger.warning(
                        f"[私聊][{self.private_name}]ReplyChecker 检测到回复与上一条 Bot 消息完全相同: '{reply}'"
                    )
                    return (
                        False,
                        "被逻辑检查拒绝：回复内容与你上一条发言完全相同，可以根据人设选择深入话题或寻找其它话题或等待",
                        True,
                    )  # 不合适，需要返回至决策层
                # 2. 相似度检查 (如果精确匹配未通过)
                import difflib  # 导入 difflib 库

                # 计算编辑距离相似度，ratio() 返回 0 到 1 之间的浮点数
                similarity_ratio = difflib.SequenceMatcher(None, reply, bot_messages[0]).ratio()
                logger.debug(f"[私聊][{self.private_name}]ReplyChecker - 相似度: {similarity_ratio:.2f}")

                # 设置一个相似度阈值
                similarity_threshold = 0.9
                if similarity_ratio > similarity_threshold:
                    logger.warning(
                        f"[私聊][{self.private_name}]ReplyChecker 检测到回复与上一条 Bot 消息高度相似 (相似度 {similarity_ratio:.2f}): '{reply}'"
                    )
                    return (
                        False,
                        f"被逻辑检查拒绝：回复内容与你上一条发言高度相似 (相似度 {similarity_ratio:.2f})，可以根据人设选择深入话题或寻找其它话题或等待。",
                        True,
                    )

        except Exception as e:
            import traceback

            logger.error(f"[私聊][{self.private_name}]检查回复时出错: 类型={type(e)}, 值={e}")
            logger.error(f"[私聊][{self.private_name}]{traceback.format_exc()}")  # 打印详细的回溯信息

        # 本轮工具执行结果（发语音/发图等）：checker 的对话快照早于工具执行，
        # 看不到这些动作——不注入的话，回复提及工具结果会被误判为"重复/不一致"
        tool_block = ""
        if tool_results_text:
            tool_block = f"""本轮已真实执行的工具动作（这些已经发生，回复提及它们属于正常反馈，不算重复发言或内容不一致）：
{tool_results_text}

"""

        prompt = f"""你是一个聊天逻辑检查器，请检查以下回复或消息是否合适：

当前对话目标：{goal}
最新的对话记录：
{chat_history_text}

{tool_block}待检查的消息：
{reply}

请结合聊天记录和你的人设检查以下几点：
注意：优先判断是否符合你的人设，其次再判断是否符合其他条目。人设和情绪有时候是会改变很多设定的，因为人对人之前的界限和认知是会改变的。（比如INFP在爱的人面前就不会那么有界限，比如在跟喜欢的人聊天时候那种平常会觉得越界的事情反而是喜欢的，会主动配合。因此要学会根据对方印象和好感决定）
0. 对方是否发送了新的消息，优先选择回复最新的消息。特殊情况除外（如你在讲述等未完成目的的情况下，对方发送了新的消息，你可以选择先讲完你的话再回复对方的消息）
1. 这条消息是否依然符合当前对话目标和实现方式（根据人设和情况来判断是否要继续当前目标还是继续对方目标）
2. 这条消息是否与最新的对话记录保持一致性（根据人设和情况来判断是否要继续当前主题还是继续对方主题），有没有推进剧情/话题（看当前情况评估，可以向目标主题推进也可以不推进）
3. 这条消息是否存在重复发言，或重复表达同质内容（尤其是只是换一种方式表达了相同的含义）
4. 这条消息是否以发送者的角度发言（不要让发送者自己回复自己的消息），回复是否以你的角度发言,不要把"你"说的话当做对方说的话，这是你自己说的话
5. 这条消息是否通俗易懂，是否符合对话的风格和语气
6. 这条消息是否有些多余，例如在对方没有回复的情况下，依然连续多次“消息轰炸”（尤其是已经连续发送3条信息的情况，这很可能不合理，需要着重判断）
7. 这条消息是否使用了完全没必要的修辞或者比喻（根据你的人设来判断，但人一般情况下是没有那么多精力来做这些修饰和比喻的）
8. 这条消息是否符合对话的逻辑（根据人设来判断是否需要符合逻辑）和顺序（根据人设和情况来判断，可以向目标主题推进也可以不推进，也可以另辟蹊径提出新主题）
9. 这条消息是否太过冗长了（通常私聊的每条消息长度在20字以内，除非特殊情况）
10. 这条消息是不是重复了人物称呼（通常情况下私聊一般都是很少称呼别人名字，大多都是以“你”来称呼对方）
11. 这条消息是否重复了聊天主题对象。（通常情况下私聊不会每一句话都会带上主题，如第一句：小黄鸭确实可爱。第二句：我也喜欢小黄鸭。第三句：小黄鸭真的很好看。这是重复了聊天主题对象。正常例子第一句：小黄鸭确实可爱。第二句：我也买了一个。第三句：你猜猜多少钱？）
12. 在连续多次发送消息的情况下，这条消息是否衔接自然，会不会显得奇怪（例如连续两条消息中部分内容重叠）



请以JSON格式输出，包含以下字段：
1. suitable: 是否合适 (true/false)
2. reason: 原因说明（80字以内的一段话，中间不要换行，检查要点不必逐条展开）
3. need_replan: 是否需要重新决策 (true/false)，当你认为此时已经不适合发消息，需要规划其它行动时，设为true

输出格式示例：
{{
    "suitable": true,
    "reason": "回复符合要求，虽然有可能略微偏离目标，但是整体内容流畅得体",
    "need_replan": false
}}

注意：请严格按照JSON格式输出，不要包含任何其他内容。
/no_think"""

        try:
            content, _ = await self.llm.generate_response_async(prompt)
            logger.debug(f"[私聊][{self.private_name}]检查回复的原始返回: {content}")

            # 清理内容，尝试提取JSON部分（平衡扫描+换行修复，GLM系reason爱塞换行/嵌套括号）
            content = content.strip()
            result = loads_with_newline_repair(content)
            if not isinstance(result, dict):
                json_span = extract_first_json_span(content, "{", "}")
                result = loads_with_newline_repair(json_span) if json_span else None

            if not isinstance(result, dict):
                # JSON彻底救不回来，从文本中判断
                is_suitable = "不合适" not in content.lower() and "违规" not in content.lower()
                reason = content[:100] if content else "无法解析响应"
                need_replan = "重新规划" in content.lower() or "目标不适合" in content.lower()
                return is_suitable, reason, need_replan

            # 验证JSON字段
            suitable = result.get("suitable", None)
            reason = result.get("reason", "未提供原因")
            need_replan = result.get("need_replan", False)

            # 如果suitable字段是字符串，转换为布尔值
            if isinstance(suitable, str):
                suitable = suitable.lower() == "true"

            # 如果suitable字段不存在或不是布尔值，从reason中判断
            if suitable is None:
                suitable = "不合适" not in reason.lower() and "违规" not in reason.lower()

            # 如果不合适且未达到最大重试次数，返回需要重试
            if not suitable and retry_count < self.max_retries:
                return False, reason, False

            # 如果不合适且已达到最大重试次数，返回需要重新规划
            if not suitable and retry_count >= self.max_retries:
                return False, f"多次重试后仍不合适: {reason}", True

            return suitable, reason, need_replan

        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]检查回复时出错: {e}")
            # 如果出错且已达到最大重试次数，建议重新规划
            if retry_count >= self.max_retries:
                return False, "多次检查失败，建议重新规划", True
            return False, f"检查过程出错，建议重试: {str(e)}", False
