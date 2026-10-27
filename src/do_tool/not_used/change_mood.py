from src.do_tool.tool_can_use.base_tool import BaseTool
from src.config.config import global_config
from src.common.logger_manager import get_logger
from src.plugins.moods.moods import MoodManager

from typing import Dict, Any

logger = get_logger("change_mood_tool")


class ChangeMoodTool(BaseTool):
    """改变心情的工具"""

    name = "change_mood"
    description = "根据收到的内容和自身回复的内容，改变心情,当你回复了别人的消息，你可以使用这个工具"
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "引起你改变心情的文本"},
            "response_set": {"type": "list", "description": "你对文本的回复"},
        },
        "required": ["text", "response_set"],
    }

    async def execute(self, function_args: Dict[str, Any], message_txt: str = "") -> Dict[str, Any]:
        """执行心情改变

        Args:
            function_args: 工具参数
            message_txt: 原始消息文本

        Returns:
            Dict: 工具执行结果
        """
        try:
            response_set = function_args.get("response_set")
            _message_processed_plain_text = function_args.get("text")

            mood_manager = MoodManager.get_instance()
            # gpt = ResponseGenerator()

            if response_set is None:
                response_set = ["你还没有回复"]

            _ori_response = ",".join(response_set)
            # _stance, emotion = await gpt._get_emotion_tags(ori_response, message_processed_plain_text)
            emotion = "平静"
            mood_manager.update_mood_from_emotion(emotion, global_config.mood_intensity_factor)
            return {"name": "change_mood", "content": f"你的心情刚刚变化了，现在的心情是: {emotion}"}
        except Exception as e:
            logger.error(f"心情改变工具执行失败: {str(e)}")
            return {"name": "change_mood", "content": f"心情改变失败: {str(e)}"}


# 注册工具
# register_tool(ChangeMoodTool)
