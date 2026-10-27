from src.plugins.models.utils_model import LLMRequest
from src.config.config import global_config
import json
from src.common.logger_manager import get_logger
from src.do_tool.tool_can_use import get_all_tool_definitions, get_tool_instance
import traceback
from src.plugins.person_info.relationship_manager import relationship_manager
from src.plugins.chat.utils import parse_text_timestamps
from src.plugins.chat.chat_stream import ChatStream
from src.heart_flow.observation import ChattingObservation

logger = get_logger("tool_use")


class ToolUser:
    def __init__(self):
        self.llm_model_tool = LLMRequest(
            model=global_config.llm_tool_use, temperature=0.2, max_tokens=1000, request_type="tool_use"
        )

    @staticmethod
    async def _build_tool_prompt(
        message_txt: str, chat_stream: ChatStream = None, observation: ChattingObservation = None
    ):
        """构建工具使用的提示词

        Args:
            message_txt: 用户消息文本
            subheartflow: 子心流对象

        Returns:
            str: 构建好的提示词
        """

        if observation:
            mid_memory_info = observation.mid_memory_info
            # print(f"intol111111111111111111111111111111111222222222222mid_memory_info：{mid_memory_info}")

        # 这些信息应该从调用者传入，而不是从self获取
        bot_name = global_config.BOT_NICKNAME
        prompt = ""
        prompt += mid_memory_info
        prompt += "你正在思考如何回复群里的消息。\n"
        prompt += "之前群里进行了如下讨论:\n"
        prompt += message_txt
        # prompt += f"你注意到{sender_name}刚刚说：{message_txt}\n"
        prompt += f"注意你就是{bot_name}，{bot_name}是你的名字。根据之前的聊天记录补充问题信息，搜索时避开你的名字。\n"
        # prompt += "必须调用 'lpmm_get_knowledge' 工具来获取知识。\n"
        prompt += "你现在需要对群里的聊天内容进行回复，请你思考应该使用什么工具，然后选择工具来对消息和你的回复进行处理，你是否需要额外的信息，比如回忆或者搜寻已有的知识，改变关系和情感，或者了解你现在正在做什么。"

        prompt = await relationship_manager.convert_all_person_sign_to_person_name(prompt)
        prompt = parse_text_timestamps(prompt, mode="lite")

        return prompt

    @staticmethod
    def _define_tools():
        """获取所有已注册工具的定义

        Returns:
            list: 工具定义列表
        """
        return get_all_tool_definitions()

    @staticmethod
    async def _execute_tool_call(tool_call):
        """执行特定的工具调用

        Args:
            tool_call: 工具调用对象
            message_txt: 原始消息文本

        Returns:
            dict: 工具调用结果
        """
        try:
            function_name = tool_call["function"]["name"]
            function_args = json.loads(tool_call["function"]["arguments"])

            # 获取对应工具实例
            tool_instance = get_tool_instance(function_name)
            if not tool_instance:
                logger.warning(f"未知工具名称: {function_name}")
                return None

            # 执行工具
            result = await tool_instance.execute(function_args)
            if result:
                # 直接使用 function_name 作为 tool_type
                tool_type = function_name
                # 兼容两种返回格式：老工具 {"content": str} / 新工具 {"status", "message"}
                content = result.get("content") or result.get("message") or ""
                # convert_to_natural_language 只有部分老工具实现了；没实现的新工具用 content 兜底（防 AttributeError 炸整个调用）
                if hasattr(tool_instance, "convert_to_natural_language"):
                    natural_language = tool_instance.convert_to_natural_language(result)
                else:
                    natural_language = str(content)[:200]

                return {
                    "tool_call_id": tool_call["id"],
                    "role": "tool",
                    "name": function_name,
                    "type": tool_type,
                    "content": str(content),
                    "natural_language": natural_language,
                }
            return None
        except Exception as e:
            logger.error(f"执行工具调用时发生错误: {str(e)}")
            return None

    async def use_tool(self, message_txt: str, chat_stream: ChatStream = None, observation: ChattingObservation = None):
        """使用工具辅助思考，判断是否需要额外信息

        Args:
            message_txt: 用户消息文本
            sender_name: 发送者名称
            chat_stream: 聊天流对象
            observation: 观察对象（可选）

        Returns:
            dict: 工具使用结果，包含结构化的信息
        """
        try:
            # 注入当前聊天上下文（供 recall_chat 等工具读取当前聊天ID）
            from .tool_context import set_chat_id

            if chat_stream is not None:
                set_chat_id(getattr(chat_stream, "stream_id", None))

            # 构建提示词
            prompt = await self._build_tool_prompt(
                message_txt=message_txt,
                chat_stream=chat_stream,
                observation=observation,
            )

            # 定义可用工具
            tools = self._define_tools()
            logger.trace(f"工具定义: {tools}")

            # 使用llm_model_tool发送带工具定义的请求
            payload = {
                "model": self.llm_model_tool.model_name,
                "messages": [{"role": "user", "content": prompt}],
                "tools": tools,
                "temperature": 0.2,
            }

            logger.trace(f"发送工具调用请求，模型: {self.llm_model_tool.model_name}")
            # 发送请求获取模型是否需要调用工具
            response = await self.llm_model_tool._execute_request(
                endpoint="/chat/completions", payload=payload, prompt=prompt
            )

            # 根据返回值数量判断是否有工具调用
            if len(response) == 3:
                content, reasoning_content, tool_calls = response
                # logger.info(f"工具思考: {tool_calls}")
                # logger.debug(f"工具思考: {content}")

                # 检查响应中工具调用是否有效
                if not tool_calls:
                    logger.debug("模型返回了空的tool_calls列表")
                    return {"used_tools": False}

                tool_calls_str = ""
                for tool_call in tool_calls:
                    tool_calls_str += f"{tool_call['function']['name']}\n"
                logger.info(
                    f"根据:\n{prompt}\n\n内容：{content}\n\n模型请求调用{len(tool_calls)}个工具: {tool_calls_str}"
                )
                tool_results = []
                structured_info = {}  # 动态生成键

                # 执行所有工具调用
                for tool_call in tool_calls:
                    result = await self._execute_tool_call(tool_call)
                    if result:
                        tool_results.append(result)
                        # 使用工具名称作为键
                        tool_name = result["name"]
                        if tool_name not in structured_info:
                            structured_info[tool_name] = []
                        structured_info[tool_name].append({"name": result["name"], "content": result["content"]})

                # 如果有工具结果，返回结构化的信息
                if structured_info:
                    natural_language = ""
                    for tool_result in tool_results:
                        natural_language += tool_result["natural_language"] + "\n"
                    logger.debug(f"工具调用收集到结构化信息: {json.dumps(structured_info, ensure_ascii=False)}")
                    return {
                        "used_tools": True,
                        "structured_info": structured_info,
                        "natural_language": natural_language,
                    }
            else:
                # 没有工具调用
                content, reasoning_content = response
                logger.debug("模型没有请求调用任何工具")

            # 如果没有工具调用或处理失败，直接返回原始思考
            return {
                "used_tools": False,
            }

        except Exception as e:
            logger.error(f"工具调用过程中出错: {str(e)}")
            logger.error(f"工具调用过程中出错: {traceback.format_exc()}")
            return {
                "used_tools": False,
                "error": str(e),
            }
