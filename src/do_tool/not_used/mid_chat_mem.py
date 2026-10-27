from src.do_tool.tool_can_use.base_tool import BaseTool
from src.common.logger import get_module_logger
from typing import Dict, Any

logger = get_module_logger("get_mid_memory_tool")


class GetMidMemoryTool(BaseTool):
    """从记忆系统中获取相关记忆的工具"""

    name = "mid_chat_mem"
    description = "之前的聊天内容概述id中获取具体信息，如果没有聊天内容概述id，就不要使用"
    parameters = {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "description": "要查询的聊天记录概述id"},
        },
        "required": ["id"],
    }

    async def execute(self, function_args: Dict[str, Any], message_txt: str = "") -> Dict[str, Any]:
        """执行记忆获取

        Args:
            function_args: 工具参数
            message_txt: 原始消息文本

        Returns:
            Dict: 工具执行结果
        """
        try:
            id = function_args.get("id")
            return {"name": "mid_chat_mem", "content": str(id)}
        except Exception as e:
            logger.error(f"聊天记录获取工具执行失败: {str(e)}")
            return {"name": "mid_chat_mem", "content": f"聊天记录获取失败: {str(e)}"}


# 注册工具
# register_tool(GetMemoryTool)
