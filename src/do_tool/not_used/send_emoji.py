from src.do_tool.tool_can_use.base_tool import BaseTool
from src.common.logger import get_module_logger

from typing import Dict, Any

logger = get_module_logger("send_emoji_tool")


class SendEmojiTool(BaseTool):
    """发送表情包的工具"""

    name = "send_emoji"
    description = "当你觉得需要表达情感，或者帮助表达，可以使用这个工具发送表情包"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "要发送的表情包描述"}},
        "required": ["text"],
    }

    async def execute(self, function_args: Dict[str, Any], message_txt: str = "") -> Dict[str, Any]:
        text = function_args.get("text", message_txt)
        return {
            "name": "send_emoji",
            "content": text,
        }
