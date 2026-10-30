import asyncio
import os
import time
from typing import Any, Dict, List, Optional

import requests

from src.common.logger_manager import get_logger
from .base_tool import BaseTool

logger = get_logger("web_search")

# DeepSeek 官方搜索 API（2026-10 上线）：与 DeepSeek API 同一套 Key
# .env 里的 DEEP_SEEK_BASE_URL 已含 /v1，直接拼接即可
SEARCH_ENDPOINT = os.getenv("DEEP_SEEK_BASE_URL", "https://api.deepseek.com/v1") + "/tools/search"
API_KEY = os.getenv("DEEP_SEEK_KEY", "")

# 冷却：全局每分钟最多 1 次搜索，防刷
_last_search_time = 0.0
COOLDOWN_SECONDS = 60

# 敏感词过滤：命中则拒绝搜索（可按需增删）
SENSITIVE_WORDS: List[str] = []

# 记账文件
LEDGER_FILE = os.path.join("logs", "web_search.log")

# 注入给 LLM 的最大条数
MAX_RESULTS = 5


def _check_sensitive(query: str) -> Optional[str]:
    for word in SENSITIVE_WORDS:
        if word and word in query:
            return word
    return None


def _record_ledger(track: str, query: str, count: int) -> None:
    try:
        os.makedirs("logs", exist_ok=True)
        with open(LEDGER_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%m-%d %H:%M:%S')}] track={track} count={count} query={query[:80]}\n")
    except Exception as e:
        logger.error(f"写入搜索记账失败: {e}")


def _do_search(query: str) -> Dict[str, Any]:
    resp = requests.post(
        SEARCH_ENDPOINT,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={"query": query[:128], "count": MAX_RESULTS, "need_content": False},
        timeout=20,
    )
    if resp.status_code != 200:
        return {"status": "failed", "message": f"搜索 API 返回 HTTP {resp.status_code}"}
    results = resp.json().get("results") or []
    return {"status": "ok", "results": results}


class WebSearchTool(BaseTool):
    name = "web_search"
    description = (
        "上网搜索实时信息：查最新网络梗、新闻、任何你不确定的事实。"
        "query 用 5~20 字的关键词组合，一次说清楚要搜什么，不要连续多次搜索相近的词。"
        "返回标题和摘要，需要阅读某个结果的正文时再用 web_fetch 抓取链接。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词，5~20字，一次说清楚"},
        },
        "required": ["query"],
    }

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        global _last_search_time

        if not API_KEY:
            return {"status": "failed", "message": "未配置搜索能力（.env 里 DEEP_SEEK_KEY 为空），请直接凭已有知识回答"}

        query = (function_args.get("query") or "").strip()
        if not query:
            return {"status": "failed", "message": "query 不能为空"}

        hit = _check_sensitive(query)
        if hit:
            return {"status": "failed", "message": "这个搜索词不合适，换个别的话题吧"}

        remain = COOLDOWN_SECONDS - (time.time() - _last_search_time)
        if remain > 0:
            return {"status": "failed", "message": f"刚搜过，冷却中（还需 {remain:.0f} 秒）"}

        try:
            result = await asyncio.to_thread(_do_search, query)
        except Exception as e:
            logger.error(f"搜索异常: {e}")
            return {"status": "failed", "message": f"搜索请求失败: {e}"}

        _last_search_time = time.time()

        if result.get("status") != "ok":
            return result

        results: List[dict] = result.get("results") or []
        _record_ledger("deepseek_api", query, len(results))
        if not results:
            return {"status": "ok", "message": f"「{query}」没有搜到结果，换个关键词或换个角度再试"}

        # [方案L二期B] 世界卡：把本次搜索摘要沉淀为知识卡（fail-open，绝不影响工具返回）
        try:
            from src.plugins.self_growth.world_cards import append_world_card

            append_world_card(
                query,
                (results[0].get("snippet") or results[0].get("summary") or "").strip()[:80],
                "web_search",
            )
        except Exception:
            pass

        lines = []
        for i, r in enumerate(results[:MAX_RESULTS], 1):
            title = r.get("title", "")
            # snippet(100-200字) 优先于 summary(600-800字)：给模型判断相关性够用了，省一半 token
            brief = (r.get("snippet") or r.get("summary") or "").strip()[:200]
            url = r.get("url", "")
            date = r.get("date") or ""
            date_str = f"（{date[:10]}）" if date else ""
            lines.append(f"{i}. [{title}]{date_str}\n{brief}\n来源: {url}")

        return {
            "status": "ok",
            "message": (
                f"「{query}」搜索结果（{len(results)}条，仅按引擎排序参考，自行判断相关性；"
                f"要读正文用 web_fetch 抓对应链接）：\n\n" + "\n\n".join(lines)
            ),
        }
