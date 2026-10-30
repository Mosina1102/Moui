import asyncio
import os
import time
from typing import Any, Dict

import requests

from src.common.logger_manager import get_logger
from .base_tool import BaseTool

logger = get_logger("web_fetch")

# DeepSeek 官方网页抓取 API：返回 Markdown 正文
FETCH_ENDPOINT = os.getenv("DEEP_SEEK_BASE_URL", "https://api.deepseek.com/v1") + "/tools/fetch"
API_KEY = os.getenv("DEEP_SEEK_KEY", "")

# 正文截断长度：防止长文撑爆上下文
MAX_CONTENT_CHARS = 4000

# 冷却：全局每分钟最多 3 次
_last_fetch_times = []
FETCH_COOLDOWN_SECONDS = 60
FETCH_COOLDOWN_LIMIT = 3


def _do_fetch(url: str) -> Dict[str, Any]:
    resp = requests.post(
        FETCH_ENDPOINT,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={"url": url},
        timeout=30,
    )
    if resp.status_code == 404:
        return {"status": "failed", "message": "抓不到这个网页（可能需要登录、被站点拦截、或页面没有正文）"}
    if resp.status_code != 200:
        return {"status": "failed", "message": f"抓取 API 返回 HTTP {resp.status_code}"}
    page = resp.json()
    return {"status": "ok", "title": page.get("title", ""), "content": page.get("content", "")}


class WebFetchTool(BaseTool):
    name = "web_fetch"
    description = (
        "抓取一个网页的正文（Markdown），用于阅读 web_search 结果里感兴趣的链接、或你已知地址的公开网页。"
        "url 必须是 http/https 开头的完整网址。需要登录的网站抓不到。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要抓取的网页完整 URL"},
        },
        "required": ["url"],
    }

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        global _last_fetch_times

        if not API_KEY:
            return {"status": "failed", "message": "未配置抓取能力（.env 里 DEEP_SEEK_KEY 为空）"}

        url = (function_args.get("url") or "").strip()
        if not url or not url.lower().startswith(("http://", "https://")):
            return {"status": "failed", "message": "url 必须以 http:// 或 https:// 开头"}

        now = time.time()
        _last_fetch_times = [t for t in _last_fetch_times if now - t < FETCH_COOLDOWN_SECONDS]
        if len(_last_fetch_times) >= FETCH_COOLDOWN_LIMIT:
            return {"status": "failed", "message": "抓取太频繁了，稍等一分钟再试"}

        try:
            result = await asyncio.to_thread(_do_fetch, url)
        except Exception as e:
            logger.error(f"抓取异常: {e}")
            return {"status": "failed", "message": f"抓取请求失败: {e}"}

        _last_fetch_times.append(now)

        if result.get("status") != "ok":
            return result

        title = result.get("title", "")
        content = result.get("content", "")
        try:
            os.makedirs("logs", exist_ok=True)
            with open(os.path.join("logs", "web_search.log"), "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%m-%d %H:%M:%S')}] track=deepseek_api fetch={url[:80]}\n")
        except Exception as e:
            logger.error(f"写入抓取记账失败: {e}")

        truncated = ""
        if len(content) > MAX_CONTENT_CHARS:
            content = content[:MAX_CONTENT_CHARS]
            truncated = "\n\n（正文过长已截断，如需后续部分请抓取分页链接或自行摘取要点）"

        # [方案L二期B] 世界卡：页面 title + 正文前80字沉淀（fail-open，绝不影响工具返回）
        try:
            if title:
                from src.plugins.self_growth.world_cards import append_world_card

                append_world_card(title, (content or "")[:80], "web_fetch")
        except Exception:
            pass

        header = f"【{title}】\n" if title else ""
        return {"status": "ok", "message": f"{header}{content}{truncated}"}
