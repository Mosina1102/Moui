from src.do_tool.tool_can_use.base_tool import BaseTool
from src.plugins.schedule.schedule_generator import bot_schedule
from src.common.logger import get_module_logger
from typing import Dict, Any
from datetime import datetime

logger = get_module_logger("get_current_task_tool")


class GetCurrentTaskTool(BaseTool):
    """获取当前正在做的事情/最近的任务工具"""

    name = "get_schedule"
    description = "获取当前正在做的事情，或者某个时间点/时间段的日程信息"
    parameters = {
        "type": "object",
        "properties": {
            "start_time": {"type": "string", "description": "开始时间，格式为'HH:MM'，填写current则获取当前任务"},
            "end_time": {"type": "string", "description": "结束时间，格式为'HH:MM'，填写current则获取当前任务"},
        },
        "required": ["start_time", "end_time"],
    }

    async def execute_task(self, task_name: str, task_args: Dict[str, Any]) -> Dict[str, Any]:
        """根据任务名称调用不同的任务处理方法

        Args:
            task_name: 任务名称
            task_args: 任务参数

        Returns:
            Dict: 任务执行结果
        """
        if task_name == "get_current_task":
            return await self._get_current_task(task_args)
        elif task_name == "get_time_range_tasks":
            return await self._get_time_range_tasks(task_args)
        else:
            raise ValueError(f"Unsupported task name: {task_name}")

    async def _get_current_task(self, task_args: Dict[str, Any]) -> Dict[str, Any]:
        """获取当前正在做的事情"""
        current_task = bot_schedule.get_current_num_task(num=1, time_info=True)
        current_time = datetime.now().strftime("%H:%M:%S")
        current_date = datetime.now().strftime("%Y-%m-%d")
        if current_task:
            task_info = f"{current_date} {current_time}，你在{current_task}"
        else:
            task_info = f"{current_time} {current_date}，没在做任何事情"
        return {"name": "get_current_task", "content": f"日程信息: {task_info}"}

    async def _get_time_range_tasks(self, task_args: Dict[str, Any]) -> Dict[str, Any]:
        """获取某个时间点/时间段的日程信息"""
        start_time = task_args.get("start_time")
        end_time = task_args.get("end_time")
        tasks = await bot_schedule.get_task_from_time_to_time(start_time, end_time)
        if tasks:
            task_list = []
            for task in tasks:
                task_time = task[0].strftime("%H:%M")
                task_content = task[1]
                task_list.append(f"{task_time}时，{task_content}")
            task_info = "\n".join(task_list)
        else:
            task_info = f"在 {start_time} 到 {end_time} 之间没有找到日程信息"
        return {"name": "get_time_range_tasks", "content": f"日程信息: {task_info}"}

    async def execute(self, function_args: Dict[str, Any], message_txt: str = "") -> Dict[str, Any]:
        """执行获取当前任务或指定时间段的日程信息

        Args:
            function_args: 工具参数
            message_txt: 原始消息文本，此工具不使用

        Returns:
            Dict: 工具执行结果
        """
        start_time = function_args.get("start_time")
        end_time = function_args.get("end_time")

        if start_time == "current" or end_time == "current":
            return await self.execute_task("get_current_task", {})
        elif start_time and end_time:
            return await self.execute_task("get_time_range_tasks", function_args)
