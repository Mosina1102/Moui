from src.common.logger import get_module_logger, LogConfig
from src.common.logger import (
    BACKGROUND_TASKS_STYLE_CONFIG,
    MAIN_STYLE_CONFIG,
    MEMORY_STYLE_CONFIG,
    PFC_STYLE_CONFIG,
    MOOD_STYLE_CONFIG,
    TOOL_USE_STYLE_CONFIG,
    RELATION_STYLE_CONFIG,
    CONFIG_STYLE_CONFIG,
    HEARTFLOW_STYLE_CONFIG,
    SCHEDULE_STYLE_CONFIG,
    LLM_STYLE_CONFIG,
    CHAT_STYLE_CONFIG,
    EMOJI_STYLE_CONFIG,
    SUB_HEARTFLOW_STYLE_CONFIG,
    SUB_HEARTFLOW_MIND_STYLE_CONFIG,
    SUBHEARTFLOW_MANAGER_STYLE_CONFIG,
    BASE_TOOL_STYLE_CONFIG,
    CHAT_STREAM_STYLE_CONFIG,
    PERSON_INFO_STYLE_CONFIG,
    WILLING_STYLE_CONFIG,
    PFC_ACTION_PLANNER_STYLE_CONFIG,
    MAI_STATE_CONFIG,
    LPMM_STYLE_CONFIG,
    HFC_STYLE_CONFIG,
    TIANYI_STYLE_CONFIG,
    REMOTE_STYLE_CONFIG,
    TOPIC_STYLE_CONFIG,
    SENDER_STYLE_CONFIG,
    CONFIRM_STYLE_CONFIG,
    MODEL_UTILS_STYLE_CONFIG,
    PROMPT_STYLE_CONFIG,
    CHANGE_MOOD_TOOL_STYLE_CONFIG,
    GET_KNOWLEDGE_TOOL_STYLE_CONFIG,
    GET_TIME_DATE_TOOL_STYLE_CONFIG,
    LPMM_GET_KNOWLEDGE_TOOL_STYLE_CONFIG,
    OBSERVATION_STYLE_CONFIG,
    MESSAGE_BUFFER_STYLE_CONFIG,
    CHAT_MESSAGE_STYLE_CONFIG,
    CHAT_IMAGE_STYLE_CONFIG,
    INIT_STYLE_CONFIG,
    SIMPLE_OUTPUT,
)

# 可根据实际需要补充更多模块配置
MODULE_LOGGER_CONFIGS = {
    "background_tasks": BACKGROUND_TASKS_STYLE_CONFIG,  # 后台任务
    "main": MAIN_STYLE_CONFIG,  # 主程序
    "memory": MEMORY_STYLE_CONFIG,  # 海马体
    "pfc": PFC_STYLE_CONFIG,  # PFC
    "mood": MOOD_STYLE_CONFIG,  # 心情
    "tool_use": TOOL_USE_STYLE_CONFIG,  # 工具使用
    "relation": RELATION_STYLE_CONFIG,  # 关系
    "config": CONFIG_STYLE_CONFIG,  # 配置
    "heartflow": HEARTFLOW_STYLE_CONFIG,  # Moui大脑袋
    "schedule": SCHEDULE_STYLE_CONFIG,  # 在干嘛
    "llm": LLM_STYLE_CONFIG,  # Moui组织语言
    "chat": CHAT_STYLE_CONFIG,  # 见闻
    "emoji": EMOJI_STYLE_CONFIG,  # 表情包
    "sub_heartflow": SUB_HEARTFLOW_STYLE_CONFIG,  # Moui水群
    "sub_heartflow_mind": SUB_HEARTFLOW_MIND_STYLE_CONFIG,  # Moui小脑袋
    "subheartflow_manager": SUBHEARTFLOW_MANAGER_STYLE_CONFIG,  # Moui水群[管理]
    "base_tool": BASE_TOOL_STYLE_CONFIG,  # 工具使用
    "chat_stream": CHAT_STREAM_STYLE_CONFIG,  # 聊天流
    "person_info": PERSON_INFO_STYLE_CONFIG,  # 人物信息
    "willing": WILLING_STYLE_CONFIG,  # 意愿
    "pfc_action_planner": PFC_ACTION_PLANNER_STYLE_CONFIG,  # PFC私聊规划
    "mai_state": MAI_STATE_CONFIG,  # Moui状态
    "lpmm": LPMM_STYLE_CONFIG,  # LPMM
    "hfc": HFC_STYLE_CONFIG,  # HFC
    "tianyi": TIANYI_STYLE_CONFIG,  # 天依
    "remote": REMOTE_STYLE_CONFIG,  # 远程
    "topic": TOPIC_STYLE_CONFIG,  # 话题
    "sender": SENDER_STYLE_CONFIG,  # 消息发送
    "confirm": CONFIRM_STYLE_CONFIG,  # EULA与PRIVACY确认
    "model_utils": MODEL_UTILS_STYLE_CONFIG,  # 模型工具
    "prompt": PROMPT_STYLE_CONFIG,  # 提示词
    "change_mood_tool": CHANGE_MOOD_TOOL_STYLE_CONFIG,  # 改变心情工具
    "get_knowledge_tool": GET_KNOWLEDGE_TOOL_STYLE_CONFIG,  # 获取知识工具
    "get_time_date": GET_TIME_DATE_TOOL_STYLE_CONFIG,  # 获取时间日期工具
    "lpm_get_knowledge_tool": LPMM_GET_KNOWLEDGE_TOOL_STYLE_CONFIG,  # LPMM获取知识工具
    "observation": OBSERVATION_STYLE_CONFIG,  # 聊天观察
    "message_buffer": MESSAGE_BUFFER_STYLE_CONFIG,  # 消息缓冲
    "chat_message": CHAT_MESSAGE_STYLE_CONFIG,  # 聊天消息
    "chat_image": CHAT_IMAGE_STYLE_CONFIG,  # 聊天图片
    "init": INIT_STYLE_CONFIG,  # 初始化
    # ...如有更多模块，继续添加...
}


# ---- 魔改模块统一样式（方案A·老大拍板）：统一结构 <time>|<level>|<频道>|<message>，后续界面按频道解析 ----
def _modded_style(channel: str) -> dict:
    tag = f"[{channel}]"
    advanced = {
        "console_format": (
            "<white>{time:YYYY-MM-DD HH:mm:ss}</white> | "
            "<level>{level: <8}</level> | "
            "<green>" + tag + "</green> | "
            "<level>{message}</level>"
        ),
        "file_format": (
            "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <15} | " + tag + " | {message}"
        ),
    }
    simple = {
        "console_format": "<green>" + tag + "</green> | <level>{message}</level>",
        "file_format": tag + " | {message}",
    }
    return simple if SIMPLE_OUTPUT else advanced


for _mod_logger, _mod_channel in {
    # 方案K：可塑性记忆家族
    "self_review": "K·自我认知",
    "self_memory": "K·自我认知库",
    "group_impression": "K·群像",
    "group_review": "K·群像审查",
    "plastic_scheduler": "K·调度",
    "daily_reflection": "K·反思",
    "score_events": "K·打分流",
    "handoff_memo": "K·交接",
    "session_pool": "K·突发会话",
    "pfc_meta": "K·元工具",
    "group_reply_checker": "发送审核",
    "unified_brain": "统一大脑",
    # 魔改工具/功能家族
    "web_search": "工具·搜索",
    "web_image": "工具·搜图",
    "web_fetch": "工具·网页",
    "send_voice": "工具·语音",
    "recall_chat": "工具·回溯",
    "qzone": "空间",
    "qzone_tool": "空间工具",
    "notebook_tool": "记事本",
    "account_action": "账号操作",
    "proactive": "主动搭话",
    "interest": "兴趣",
    "outbound_enhance": "发送增强",
    "backup": "工程·备份",
}.items():
    MODULE_LOGGER_CONFIGS[_mod_logger] = _modded_style(_mod_channel)


def get_logger(module_name: str):
    style_config = MODULE_LOGGER_CONFIGS.get(module_name)
    if style_config:
        log_config = LogConfig(
            console_format=style_config["console_format"],
            file_format=style_config["file_format"],
        )
        return get_module_logger(module_name, config=log_config)
    # 若无特殊样式，使用默认
    return get_module_logger(module_name)
