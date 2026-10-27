from src.do_tool.tool_can_use.base_tool import BaseTool
from src.plugins.memory_system.Hippocampus import HippocampusManager
from src.common.logger import get_module_logger
from typing import Dict, Any

logger = get_module_logger("mid_chat_mem_tool")


class GetMemoryTool(BaseTool):
    """从记忆系统中获取相关记忆的工具"""

    name = "get_memory"
    description = (
        "从自己的长期记忆里找相关内容。当话题涉及你们过去的经历、约定、聊过的事，"
        "或者你想起来'好像有过这事'的时候使用。topic 传主题关键词。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "要查询的相关主题,用逗号隔开"},
            "max_memory_num": {"type": "integer", "description": "最大返回记忆数量"},
        },
        "required": ["topic"],
    }

    def convert_to_natural_language(self, result: Dict[str, Any]) -> str:
        """将记忆结果转换为自然语言格式
        
        Args:
            result: 记忆结果字典
            
        Returns:
            str: 自然语言格式的结果
        """
        if not result:
            return "无法获取记忆信息"
            
        content = result.get("content", "")
        if content:
            return f"你的记忆中有：{content}"
        return "无法获取记忆信息"

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        """执行记忆获取

        Args:
            function_args: 工具参数
            message_txt: 原始消息文本

        Returns:
            Dict: 工具执行结果
        """
        try:
            topic = function_args.get("topic")
            max_memory_num = function_args.get("max_memory_num", 2)

            # 将主题字符串转换为列表
            topic_list = topic.split(",")

            # 调用记忆系统
            related_memory = await HippocampusManager.get_instance().get_memory_from_topic(
                valid_keywords=topic_list, max_memory_num=max_memory_num, max_memory_length=2, max_depth=3
            )

            memory_info = ""
            if related_memory:
                for memory in related_memory:
                    memory_info += memory[1] + "\n"

            if memory_info:
                content = f"你记得这些事情: {memory_info}\n"
                content += "以上是你的回忆，不一定是目前聊天里的人说的，也不一定是现在发生的事情，请记住。\n"

            else:
                content = f"{topic}的记忆，你记不太清"

            return {"name": "get_memory", "content": content}
        except Exception as e:
            logger.error(f"记忆获取工具执行失败: {str(e)}")
            return {"name": "get_memory", "content": f"记忆获取失败: {str(e)}"}


# 注册工具
# register_tool(GetMemoryTool)
