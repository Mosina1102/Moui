"""网上找图发图工具：Bing 图片搜索 → 下载 → VLM 审核 → 直接发送给对方。

工具内完成整个动作（send_poke 模式）：模型调用后图已经发出，返回描述给模型。
chat_id 从 tool_context（contextvars）读取，适配群聊与私聊。
"""
import asyncio
import hashlib
import os
import random
import re
import time
from typing import Any, Dict, List, Optional

import requests

from src.common.logger_manager import get_logger
from .base_tool import BaseTool

logger = get_logger("web_image")

# 保存目录
SAVE_DIR = os.path.join("data", "images_web")

# 搜索与下载参数
BING_IMAGE_URL = "https://cn.bing.com/images/search"
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024  # 5MB
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_ATTEMPTS = 3  # 依次尝试下载前 N 个结果

_last_use_time = 0.0
COOLDOWN_SECONDS = 60

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def _bing_image_search(query: str) -> List[str]:
    """Bing 图片搜索，返回候选原图 URL 列表（murl 字段）"""
    import html as html_lib

    resp = requests.get(
        BING_IMAGE_URL,
        params={"q": query[:64], "first": 1, "count": 20},
        headers=_HEADERS,
        timeout=15,
    )
    if resp.status_code != 200:
        logger.warning(f"Bing 图片搜索 HTTP {resp.status_code}")
        return []
    # 页面里图片元数据以 HTML 实体转义的 JSON 内嵌（murl&quot;:&quot;...）
    raw_urls = re.findall(r'murl&quot;:&quot;(.*?)&quot;', resp.text)
    urls = []
    for u in raw_urls:
        fixed = html_lib.unescape(u).replace("\\/", "/").replace("\\u0026", "&")
        if fixed.startswith("http") and fixed not in urls:
            urls.append(fixed)
    return urls


def _download_image(url: str) -> Optional[str]:
    """下载图片到本地，校验格式与大小，返回本地路径或 None"""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15, stream=True)
        if resp.status_code != 200:
            return None
        content_type = resp.headers.get("Content-Type", "")
        if "image" not in content_type:
            return None
        data = resp.content
        if not (2 * 1024 <= len(data) <= MAX_DOWNLOAD_BYTES):
            return None
        ext = os.path.splitext(url.split("?")[0])[1].lower()
        if ext not in ALLOWED_EXT:
            ext = ".jpg"
        os.makedirs(SAVE_DIR, exist_ok=True)
        filename = f"{int(time.time())}_{hashlib.md5(data).hexdigest()[:10]}{ext}"
        path = os.path.join(SAVE_DIR, filename)
        with open(path, "wb") as f:
            f.write(data)
        return path
    except Exception as e:
        logger.debug(f"下载图片失败({url[:60]}): {e}")
        return None


async def _vlm_review(path: str) -> Optional[str]:
    """VLM 审核+描述：返回描述文本（适合发送）或 None（不适合/出错）。"""
    try:
        from ...config.config import global_config
        from ...plugins.models.utils_model import LLMRequest
        from ...plugins.chat.utils_image import image_path_to_base64

        b64 = image_path_to_base64(path)
        if not b64:
            return None
        image_format = os.path.splitext(path)[1].lstrip(".").replace("jpg", "jpeg") or "jpeg"
        llm = LLMRequest(model=global_config.vlm, temperature=0.3, max_tokens=120, request_type="web_image_review")
        prompt = (
            "用一句话描述这张图的内容（30字内）。"
            "如果图里包含血腥暴力、色情裸露、令人不适的内容，只回答：不适合。"
        )
        resp = await llm.generate_response_for_image(prompt, b64, image_format)
        response = resp[0] if isinstance(resp, tuple) else resp
        text = (response or "").strip()
        if not text or "不适合" in text:
            return None
        return text[:60]
    except Exception as e:
        logger.warning(f"VLM 审核图片失败: {e}")
        return None


class WebImageTool(BaseTool):
    name = "search_web_image"
    description = (
        "上网找一张图发给对方看（表情包/梗图/实物图/壁纸等）。"
        "当你想给对方看一个画面、配一张图表达情绪、对方问你'长什么样'时调用。"
        "query 用具体的画面描述词。图片会自动搜索、审核并发送到当前聊天，返回图片内容描述。"
        "发图时想配一句话就填 caption（像平时说话，如'早！给你看个日出'），"
        "图发出去后你的话会紧跟发出——真人发图都带句话，别光甩图。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "想找什么图的画面描述，如'柴犬歪头表情包'、'冰墩墩图片'"},
            "caption": {"type": "string", "description": "可选。随图配的一句话（30字内，像平时说话）"},
        },
        "required": ["query"],
    }

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        global _last_use_time

        query = (function_args.get("query") or "").strip()
        if not query:
            return {"status": "failed", "message": "query 不能为空"}

        remain = COOLDOWN_SECONDS - (time.time() - _last_use_time)
        if remain > 0:
            return {"status": "failed", "message": f"刚发过图，冷却中（还需 {remain:.0f} 秒）"}

        # 1. 搜索（阻塞网络 IO 放线程）
        try:
            urls = await asyncio.to_thread(_bing_image_search, query)
        except Exception as e:
            logger.error(f"Bing 图片搜索异常: {e}")
            return {"status": "failed", "message": "网上搜图失败了，下次再试吧"}

        if not urls:
            return {"status": "failed", "message": f"「{query}」没搜到合适的图，换个描述再试"}

        # 2. 依次尝试下载
        random.shuffle(urls)
        local_path = None
        for url in urls[:MAX_ATTEMPTS * 3]:
            local_path = await asyncio.to_thread(_download_image, url)
            if local_path:
                break
        if not local_path:
            return {"status": "failed", "message": "图找到了但都下载失败，下次再试吧"}

        # 3. VLM 审核+描述（不适合/出错则放弃本图）
        desc = await _vlm_review(local_path)
        if not desc:
            logger.info(f"[web_image] 图片未过审核，删除: {local_path}")
            try:
                os.remove(local_path)
            except OSError:
                pass
            return {"status": "failed", "message": "找到的图不太合适，没发出去"}

        # 4. 发送到当前聊天
        try:
            from ..tool_context import get_chat_id
            from ...plugins.chat.chat_stream import chat_manager
            from ...plugins.chat.message import MessageSending
            from ...plugins.chat.utils_image import image_path_to_base64
            from ...plugins.message.api import global_api
            from ...plugins.storage.storage import MessageStorage
            from ...config.config import global_config
            from maim_message import UserInfo, Seg

            # 优先显式传参（PFC/统一大脑塞进 function_args 的），回落上下文
            chat_id = function_args.get("chat_id") or get_chat_id()
            chat_stream = chat_manager.get_stream(chat_id) if chat_id else None
            if not chat_stream:
                return {"status": "failed", "message": "拿不到当前聊天上下文，图没发出去"}

            b64 = image_path_to_base64(local_path)
            if not b64:
                return {"status": "failed", "message": "图片读取失败"}

            bot_user_info = UserInfo(
                user_id=global_config.BOT_QQ,
                user_nickname=global_config.BOT_NICKNAME,
                platform=chat_stream.platform,
            )
            message = MessageSending(
                message_id=f"webimg{round(time.time(), 2)}",
                chat_stream=chat_stream,
                bot_user_info=bot_user_info,
                sender_info=None,
                message_segment=Seg(type="emoji", data=b64),
                reply=None,
                is_head=False,
                is_emoji=True,
            )
            # 手动给入库文本（不走 process()，emoji 段会触发一次无意义的 VLM 描述）
            message.processed_plain_text = f"[表情包] {desc[:50]}"
            message.detailed_plain_text = f"[表情包] {desc[:50]}"
            await global_api.send_message(message)
            await MessageStorage().store_message(message, chat_stream)

            # 随图配文（caption）：图发出后紧跟一条她的话——真人发图九成带句词，光甩图像卡了
            caption = (function_args.get("caption") or "").strip()
            if caption:
                cap_message = MessageSending(
                    message_id=f"webimgcap{round(time.time(), 2)}",
                    chat_stream=chat_stream,
                    bot_user_info=bot_user_info,
                    sender_info=None,
                    message_segment=Seg(type="text", data=caption[:60]),
                    reply=None,
                    is_head=False,
                    is_emoji=False,
                )
                await global_api.send_message(cap_message)
                await MessageStorage().store_message(cap_message, chat_stream)
                logger.info(f"[web_image] 随图配文已发送: {caption[:40]}")
        except Exception as e:
            logger.error(f"发送网络图片失败: {e}")
            return {"status": "failed", "message": "图下好了但发送失败了"}

        _last_use_time = time.time()
        logger.info(f"[web_image] 已发送网图: {os.path.basename(local_path)} ({desc})")
        return {"status": "ok", "message": f"图已经发给对方了（图片内容：{desc}）"}
