from typing import List, Tuple
from src.common.logger import get_module_logger
from src.common.database import db
from src.plugins.memory_system.Hippocampus import HippocampusManager
from ..models.utils_model import LLMRequest
from ...config.config import global_config
from ..chat.message import Message
from ..chat.utils import get_embedding
from ..knowledge.knowledge_lib import qa_manager
from ..utils.chat_message_builder import build_readable_messages

logger = get_module_logger("knowledge_fetcher")


class KnowledgeFetcher:
    """知识调取器"""

    def __init__(self, private_name: str):
        self.llm = LLMRequest(
            model=global_config.llm_normal,
            temperature=global_config.llm_normal["temp"],
            max_tokens=1000,
            request_type="knowledge_fetch",
        )
        self.private_name = private_name

    def _lpmm_get_knowledge(self, query: str) -> Tuple[str, str]:
        """从LPMM知识库获取相关知识

        Args:
            query: 查询内容

        Returns:
            Tuple[str, str]: (知识内容, 来源描述)
        """
        logger.debug(f"[私聊][{self.private_name}]正在从LPMM知识库中获取知识")
        try:
            knowledge_info = qa_manager.get_knowledge(query)
            if knowledge_info is None:
                logger.debug(f"[私聊][{self.private_name}]LPMM知识库返回None，可能未初始化")
                return "", "LPMM知识库未初始化"
            logger.debug(f"[私聊][{self.private_name}]LPMM知识库查询结果: {str(knowledge_info)[:150]}")
            return knowledge_info, "LPMM知识库"
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]LPMM知识库搜索工具执行失败: {str(e)}")
            return "", "LPMM知识库查询失败"

    async def _mongodb_get_knowledge(self, query: str, limit: int = 3, threshold: float = 0.4) -> Tuple[str, str]:
        """从MongoDB知识库获取相关知识

        Args:
            query: 查询内容
            limit: 最大返回结果数
            threshold: 相似度阈值

        Returns:
            Tuple[str, str]: (知识内容, 来源描述)
        """
        logger.debug(f"[私聊][{self.private_name}]正在从MongoDB知识库中获取知识")
        try:
            embedding = await get_embedding(query, request_type="info_retrieval")
            if not embedding:
                logger.warning(f"[私聊][{self.private_name}]无法获取查询的嵌入向量")
                return "", "MongoDB知识库无法获取嵌入向量"

            pipeline = [
                {
                    "$addFields": {
                        "dotProduct": {
                            "$reduce": {
                                "input": {"$range": [0, {"$size": "$embedding"}]},
                                "initialValue": 0,
                                "in": {
                                    "$add": [
                                        "$$value",
                                        {
                                            "$multiply": [
                                                {"$arrayElemAt": ["$embedding", "$$this"]},
                                                {"$arrayElemAt": [embedding, "$$this"]},
                                            ]
                                        },
                                    ]
                                },
                            }
                        },
                        "magnitude1": {
                            "$sqrt": {
                                "$reduce": {
                                    "input": "$embedding",
                                    "initialValue": 0,
                                    "in": {"$add": ["$$value", {"$multiply": ["$$this", "$$this"]}]},
                                }
                            }
                        },
                        "magnitude2": {
                            "$sqrt": {
                                "$reduce": {
                                    "input": embedding,
                                    "initialValue": 0,
                                    "in": {"$add": ["$$value", {"$multiply": ["$$this", "$$this"]}]},
                                }
                            }
                        },
                    }
                },
                {"$addFields": {"similarity": {"$divide": ["$dotProduct", {"$multiply": ["$magnitude1", "$magnitude2"]}]}}},
                {"$match": {"similarity": {"$gte": threshold}}},
                {"$sort": {"similarity": -1}},
                {"$limit": limit},
                {"$project": {"content": 1, "similarity": 1, "source_file": 1}},
            ]

            results = list(db.knowledges.aggregate(pipeline))
            logger.debug(f"[私聊][{self.private_name}]MongoDB知识库查询结果数量: {len(results)}")

            if not results:
                return "", "MongoDB知识库未找到匹配"

            knowledge_parts = []
            for result in results:
                knowledge_parts.append(result.get("content", ""))

            return "\n".join(knowledge_parts), "MongoDB知识库"
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]MongoDB知识库搜索失败: {str(e)}")
            return "", "MongoDB知识库查询失败"

    async def _web_search_knowledge(self, query: str) -> Tuple[str, str]:
        """联网搜索（DeepSeek Search API）——本地知识库为空时的主力知识源"""
        try:
            from ...do_tool.tool_can_use.web_search_tool import WebSearchTool

            result = await WebSearchTool().execute({"query": query[:64]})
            if result.get("status") == "ok":
                return str(result.get("message", "")), "网络搜索"
            logger.debug(f"[私聊][{self.private_name}]网络搜索未成功: {result.get('message', '')}")
            return "", "网络搜索失败"
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]网络搜索异常: {e}")
            return "", "网络搜索失败"

    async def fetch(self, query: str, chat_history: List[Message]) -> Tuple[str, str]:
        """获取相关知识

        Args:
            query: 查询内容
            chat_history: 聊天历史

        Returns:
            Tuple[str, str]: (获取的知识, 知识来源)
        """
        chat_history_text = await build_readable_messages(
            chat_history,
            replace_bot_name=True,
            merge_messages=False,
            timestamp_mode="relative",
            read_mark=0.0,
        )

        related_memory = await HippocampusManager.get_instance().get_memory_from_text(
            text=f"{query}\n{chat_history_text}",
            max_memory_num=3,
            max_memory_length=2,
            max_depth=3,
            fast_retrieval=False,
        )
        knowledge_text = ""
        sources_text = "无记忆匹配"
        if related_memory:
            sources = []
            for memory in related_memory:
                knowledge_text += memory[1] + "\n"
                sources.append(f"记忆片段{memory[0]}")
            knowledge_text = knowledge_text.strip()
            sources_text = "，".join(sources)

        knowledge_text += "\n现在有以下**知识**可供参考：\n "

        lpmm_knowledge, lpmm_source = self._lpmm_get_knowledge(query)
        mongodb_knowledge, mongodb_source = await self._mongodb_get_knowledge(query)

        all_knowledge = []
        all_sources = []

        if lpmm_knowledge:
            all_knowledge.append(lpmm_knowledge)
            all_sources.append(lpmm_source)

        if mongodb_knowledge:
            all_knowledge.append(mongodb_knowledge)
            all_sources.append(mongodb_source)

        # 联网搜索兜底：本地库（LPMM/MongoDB）为空或不全时，用网络搜索补上
        web_knowledge, web_source = await self._web_search_knowledge(query)
        if web_knowledge:
            all_knowledge.append(web_knowledge)
            all_sources.append(web_source)

        if all_knowledge:
            knowledge_text += "\n".join(all_knowledge)
            if sources_text == "无记忆匹配":
                sources_text = "、".join(all_sources)
            else:
                sources_text += "、" + "、".join(all_sources)
        else:
            knowledge_text += "未找到匹配的知识"
            if sources_text == "无记忆匹配":
                sources_text = "无知识匹配"

        knowledge_text += "\n请记住这些**知识**，并根据**知识**回答问题。\n"

        return knowledge_text or "未找到相关知识", sources_text or "无记忆匹配"
