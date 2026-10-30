from src.do_tool.tool_can_use.base_tool import BaseTool
from src.common.logger_manager import get_logger
from typing import Dict, Any
from datetime import datetime

logger = get_logger("get_time_date")


class GetCurrentDateTimeTool(BaseTool):
    """获取当前时间、日期、年份和星期的工具"""

    name = "get_current_date_time"
    description = "当有人询问或者涉及到具体时间或者日期的时候，必须使用这个工具"
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        """执行获取当前时间、日期、年份和星期

        Args:
            function_args: 工具参数（此工具不使用）
            message_txt: 原始消息文本（此工具不使用）

        Returns:
            Dict: 工具执行结果
        """
        current_time = datetime.now().strftime("%H:%M:%S")
        current_date = datetime.now().strftime("%Y-%m-%d")
        current_year = datetime.now().strftime("%Y")
        current_weekday = datetime.now().strftime("%A")

        return {
            "name": "get_current_date_time",
            "content": f"当前时间: {current_time}, 日期: {current_date}, 年份: {current_year}, 星期: {current_weekday}",
        }
        
    def convert_to_natural_language(self, result: Dict[str, Any]) -> str:
        """将日期时间结果转换为自然语言格式
        
        Args:
            result: 日期时间结果字典
            
        Returns:
            str: 自然语言格式的结果
        """
        if not result:
            return "无法获取当前时间信息"
            
        content = result.get("content", "")
        if content:
            return f"当前时间是：{content}"
        return "无法获取当前时间信息"
