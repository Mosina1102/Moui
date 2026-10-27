from typing import List, Optional


from ..models.utils_model import LLMRequest
from ...config.config import global_config
from src.common.logger import get_module_logger, LogConfig, TOPIC_STYLE_CONFIG

# 定义日志配置
topic_config = LogConfig(
    # 使用海马体专用样式
    console_format=TOPIC_STYLE_CONFIG["console_format"],
    file_format=TOPIC_STYLE_CONFIG["file_format"],
)

logger = get_module_logger("topic_identifier", config=topic_config)


class TopicIdentifier:
    def __init__(self):
        self.llm_topic_judge = LLMRequest(model=global_config.llm_topic_judge, request_type="topic")

    async def identify_topic_llm(self, text: str) -> Optional[List[str]]:
        """识别消息主题，返回主题列表"""

        prompt = f"""判断这条消息的主题，如果没有明显主题请回复"无主题"，要求：
1. 主题通常2-4个字，必须简短，要求精准概括，不要太具体。
2. 建议给出多个主题，之间用英文逗号分割。只输出主题本身就好，不要有前后缀。

消息内容：{text}"""

        # 使用 LLMRequest 类进行请求
        try:
            topic, _, _ = await self.llm_topic_judge.generate_response(prompt)
        except Exception as e:
            logger.error(f"LLM 请求topic失败: {e}")
            return None
        if not topic:
            logger.error("LLM 得到的topic为空")
            return None

        # 直接在这里处理主题解析
        if not topic or topic == "无主题":
            return None

        # 解析主题字符串为列表
        topic_list = [t.strip() for t in topic.split(",") if t.strip()]

        logger.info(f"主题: {topic_list}")
        return topic_list if topic_list else None


topic_identifier = TopicIdentifier()
