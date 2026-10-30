import traceback
from typing import TYPE_CHECKING

from src.common.logger_manager import get_logger
from src.plugins.models.utils_model import LLMRequest
from src.individuality.individuality import Individuality
from src.plugins.utils.prompt_builder import global_prompt_manager, Prompt
from src.config.config import global_config
from src.do_tool.tool_can_use.notebook_tool import NotebookParser, get_notebook_context

if TYPE_CHECKING:
    from src.heart_flow.subheartflow_manager import SubHeartflowManager
    from src.heart_flow.mai_state_manager import MaiStateInfo


logger = get_logger("sub_heartflow_mind")

Prompt(
    """你是{bot_name}，{personality_info}。
当前时间：{schedule_info}
当前心情：{mood_info}

{notebook_context}

你之前的想法是：{current_thinking_info}

你现在的子心流想法：
{sub_flows_info}

请基于以上信息，生成你现在的内心独白（想法）。
要求：
1. 用第一人称思考
2. 简短自然，像人类的心理活动
3. 可以包含：对当前情况的看法、想做的事情、对某人的想法等
4. 如果需要记录重要信息，使用记事本标签（在想法末尾）：
   - 添加：<note action="add" category="类别" title="标题">内容</note>
   - 删除：<note action="delete" note_id="笔记ID"></note>
   - 完成：<note action="complete" note_id="笔记ID"></note>
   类别：reminder(提醒)、game_state(游戏状态)、user_info(用户信息)、task(任务)、other(其他)
5. 记事本管理原则：
   - 添加前先检查是否有类似的旧笔记需要清理
   - 用户改口或任务过期时主动删除旧笔记
   - 保持记事本精简，不要堆积无用内容

示例：
- 记录新任务：<note action="add" category="task" title="待会打招呼">新人来了说早上好</note>
- 清理旧笔记：<note action="delete" note_id="note_xxx"></note>

请直接输出你的想法，不要输出其他内容。""",
    "thinking_prompt",
)

Prompt(
    """你是{bot_name}，{personality_info}。
当前心情：{mood_info}

你的主心流想法：{current_mind}

以下是各个子心流的想法：
{minds_str}

请将这些子心流想法汇总成一个简洁的主想法。
要求：
1. 保留重要信息
2. 去除重复内容
3. 保持第一人称
4. 简短自然

请直接输出汇总后的想法，不要输出其他内容。""",
    "mind_summary_prompt",
)


class Mind:
    """封装 Mai 的思考过程，包括生成内心独白和汇总想法。"""

    def __init__(self, subheartflow_manager: "SubHeartflowManager", llm_model: LLMRequest):
        self.subheartflow_manager = subheartflow_manager
        self.llm_model = llm_model
        self.individuality = Individuality.get_instance()

    async def do_a_thinking(self, current_main_mind: str, mai_state_info: "MaiStateInfo", schedule_info: str):
        """
        执行一次主心流思考过程，生成新的内心独白。

        Args:
            current_main_mind: 当前的主心流想法。
            mai_state_info: 当前的 Mai 状态信息 (用于获取 mood)。
            schedule_info: 当前的日程信息。

        Returns:
            str: 生成的新的内心独白，如果出错则返回提示信息。
        """
        logger.debug("Mind: 执行思考...")

        personality_info = (
            self.individuality.get_prompt_snippet()
            if hasattr(self.individuality, "get_prompt_snippet")
            else self.individuality.personality.personality_core
        )
        mood_info = mai_state_info.get_mood_prompt()
        related_memory_info = "memory"

        try:
            sub_flows_info = await self._get_subflows_summary(current_main_mind, mai_state_info)
        except Exception as e:
            logger.error(f"[Mind Thinking] 获取子心流想法汇总失败: {e}")
            logger.error(traceback.format_exc())
            sub_flows_info = "(获取子心流想法时出错)"

        notebook_context = get_notebook_context()
        if not notebook_context:
            notebook_context = "(记事本为空)"

        try:
            prompt = (await global_prompt_manager.get_prompt_async("thinking_prompt")).format(
                bot_name=global_config.BOT_NICKNAME,
                schedule_info=schedule_info,
                personality_info=personality_info,
                related_memory_info=related_memory_info,
                current_thinking_info=current_main_mind,
                sub_flows_info=sub_flows_info,
                mood_info=mood_info,
                notebook_context=notebook_context,
            )
        except Exception as e:
            logger.error(f"[Mind Thinking] 格式化 thinking_prompt 失败: {e}")
            return "(思考时格式化Prompt出错...)"

        try:
            response, reasoning_content = await self.llm_model.generate_response_async(prompt)
            if not response:
                logger.warning("[Mind Thinking] 内心独白 LLM 返回空结果。")
                response = "(暂时没什么想法...)"
            
            if NotebookParser.has_note_markers(response):
                response, note_results = NotebookParser.extract_and_process(response)
                for result in note_results:
                    if result.get("success"):
                        logger.info(f"[记事本] {result.get('message')}")
                    else:
                        logger.warning(f"[记事本] {result.get('message')}")
            
            logger.info(f"Mind: 新想法生成: {response[:100]}...")
            return response
        except Exception as e:
            logger.error(f"[Mind Thinking] 内心独白 LLM 调用失败: {e}")
            logger.error(traceback.format_exc())
            return "(思考时调用LLM出错...)"

    async def _get_subflows_summary(self, current_main_mind: str, mai_state_info: "MaiStateInfo") -> str:
        """获取所有活跃子心流的想法，并使用 LLM 进行汇总。"""
        sub_minds_list = self.subheartflow_manager.get_active_subflow_minds()

        if not sub_minds_list:
            return "(当前没有活跃的子心流想法)"

        minds_str = "\n".join([f"- {mind}" for mind in sub_minds_list])
        logger.debug(f"Mind: 获取到 {len(sub_minds_list)} 个子心流想法进行汇总。")

        personality_info = (
            self.individuality.get_prompt_snippet()
            if hasattr(self.individuality, "get_prompt_snippet")
            else self.individuality.personality.personality_core
        )
        mood_info = mai_state_info.get_mood_prompt()
        bot_name = global_config.BOT_NICKNAME

        try:
            prompt = (await global_prompt_manager.get_prompt_async("mind_summary_prompt")).format(
                personality_info=personality_info,
                bot_name=bot_name,
                current_mind=current_main_mind,
                minds_str=minds_str,
                mood_info=mood_info,
            )
        except Exception as e:
            logger.error(f"[Mind Summary] 格式化 mind_summary_prompt 失败: {e}")
            return "(汇总想法时格式化Prompt出错...)"

        try:
            response, reasoning_content = await self.llm_model.generate_response_async(prompt)
            if not response:
                logger.warning("[Mind Summary] 想法汇总 LLM 返回空结果。")
                return "(想法汇总失败...)"
            logger.debug(f"Mind: 子想法汇总完成: {response[:100]}...")
            return response
        except Exception as e:
            logger.error(f"[Mind Summary] 想法汇总 LLM 调用失败: {e}")
            logger.error(traceback.format_exc())
            return "(想法汇总时调用LLM出错...)"

    def update_subflows_with_main_mind(self, main_mind: str):
        """触发 SubHeartflowManager 更新所有子心流的主心流信息。"""
        logger.debug("Mind: 请求更新子心流的主想法信息。")
        self.subheartflow_manager.update_main_mind_in_subflows(main_mind)
