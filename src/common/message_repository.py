from src.common.database import db
from src.common.logger import get_module_logger
import traceback
from typing import List, Dict, Any, Optional

logger = get_module_logger(__name__)


def find_messages(
    filter: Dict[str, Any], sort: Optional[List[tuple[str, int]]] = None, limit: int = 0, limit_mode: str = "latest"
) -> List[Dict[str, Any]]:
    """
    根据提供的过滤器、排序和限制条件查找消息。

    Args:
        filter: MongoDB 查询过滤器。
        sort: MongoDB 排序条件列表，例如 [('time', 1)]。仅在 limit 为 0 时生效。
        limit: 返回的最大文档数，0表示不限制。
        limit_mode: 当 limit > 0 时生效。 'earliest' 表示获取最早的记录， 'latest' 表示获取最新的记录（结果仍按时间正序排列）。默认为 'latest'。

    Returns:
        消息文档列表，如果出错则返回空列表。
    """
    try:
        query = db.messages.find(filter)
        results: List[Dict[str, Any]] = []

        if limit > 0:
            if limit_mode == "earliest":
                # 获取时间最早的 limit 条记录，已经是正序
                query = query.sort([("time", 1)]).limit(limit)
                results = list(query)
            else:  # 默认为 'latest'
                # 获取时间最晚的 limit 条记录
                query = query.sort([("time", -1)]).limit(limit)
                latest_results = list(query)
                # 将结果按时间正序排列
                # 假设消息文档中总是有 'time' 字段且可排序
                results = sorted(latest_results, key=lambda msg: msg.get("time"))
        else:
            # limit 为 0 时，应用传入的 sort 参数
            if sort:
                query = query.sort(sort)
            results = list(query)

        return results
    except Exception as e:
        log_message = (
            f"查找消息失败 (filter={filter}, sort={sort}, limit={limit}, limit_mode={limit_mode}): {e}\n"
            + traceback.format_exc()
        )
        logger.error(log_message)
        return []


def count_messages(filter: Dict[str, Any]) -> int:
    """
    根据提供的过滤器计算消息数量。

    Args:
        filter: MongoDB 查询过滤器。

    Returns:
        符合条件的消息数量，如果出错则返回 0。
    """
    try:
        count = db.messages.count_documents(filter)
        return count
    except Exception as e:
        log_message = f"计数消息失败 (filter={filter}): {e}\n" + traceback.format_exc()
        logger.error(log_message)
        return 0


# 你可以在这里添加更多与 messages 集合相关的数据库操作函数，例如 find_one_message, insert_message 等。
