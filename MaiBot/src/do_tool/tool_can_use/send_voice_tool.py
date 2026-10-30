"""发语音工具（方案E）：把想说的话用克隆音色合成后，直接发 QQ 语音消息。

链路：CosyVoice2 合成（硅基 /v1/audio/speech）→ base64 → Seg(type="voice") → 适配器 record 段。
chat_id 从 tool_context（contextvars）读取，适配群聊与私聊。不限频（她想发就发）。
"""
import asyncio
import base64
import hashlib
import os
import re
import time
from typing import Any, Dict

import requests

from src.common.logger_manager import get_logger
from .base_tool import BaseTool

logger = get_logger("send_voice")

# 硅基 TTS 配置
TTS_ENDPOINT = "https://api.siliconflow.cn/v1/audio/speech"
TTS_MODEL = "FunAudioLLM/CosyVoice2-0.5B"
_SAVE_DIR = os.path.join("data", "voices")


class SendVoiceTool(BaseTool):
    name = "send_voice"
    description = (
        "发一条语音消息（用你自己的声音说出来）。"
        "适合：道晚安/道早安、说不清楚的情绪、想撒娇或者想让人觉得你真的在说话的时候。"
        "text 是你要说的话（60字以内，口语化）。不限次数，但别连着刷屏。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要用语音说的话，60字以内，口语化"},
        },
        "required": ["text"],
    }

    @classmethod
    def is_available(cls) -> bool:
        """TTS 未启用（config [tts] 段没配）时不把本工具注入 LLM 工具表，免得白想想用"""
        try:
            from ...config.config import global_config

            return bool(global_config.tts_enable and global_config.tts_voice_uri)
        except Exception:
            return False

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        from ...config.config import global_config

        text = (function_args.get("text") or "").strip()
        if not text:
            logger.warning("[send_voice] 失败: text 为空")
            return {"status": "failed", "message": "text 不能为空"}

        # 语音文本净化：上游模型偶发在 text 里混入标记/括号描述/换行/内心独白——TTS 会照读出来
        # 去掉 [xxx] 标记（图片描述/引用标记）、<note>、括号内容、换行，只留干净口语
        text = re.sub(r"\[[^\]]*\]", "", text)
        text = re.sub(r"<note[^>]*>.*?</note>", "", text, flags=re.DOTALL)
        text = re.sub(r"[（(][^（）()]*[）)]", "", text)
        text = re.sub(r"\s+", " ", text).strip(" ，。,.")
        text = text.replace("内心想法：", "").replace("（心里想的）", "").strip()
        if not text:
            logger.warning("[send_voice] 失败: 净化后 text 为空（原始内容全是标记/括号）")
            return {"status": "failed", "message": "语音内容净化后为空，直接打字回复吧"}

        if not global_config.tts_enable or not global_config.tts_voice_uri:
            logger.warning(
                f"[send_voice] 失败: 语音功能未启用（tts_enable={global_config.tts_enable}, "
                f"voice_uri={'有' if global_config.tts_voice_uri else '空'}）"
                "——检查部署机 bot_config.toml 是否有 [tts] 段"
            )
            return {"status": "failed", "message": "语音功能未启用（config [tts] 段未配置）"}

        # 1. TTS 合成（硅基偶发 200+空响应，重试自愈）
        mp3_bytes = None
        text_clipped = text[:80]
        for attempt in range(1, 4):
            try:
                logger.info(f"[send_voice] 开始合成(第{attempt}次): {text_clipped[:40]}")
                _key = os.environ.get("SILICONFLOW_KEY", "")
                resp = await asyncio.to_thread(
                    requests.post,
                    "https://api.siliconflow.cn/v1/audio/speech",
                    headers={"Authorization": f"Bearer {_key}"},
                    json={
                        "model": TTS_MODEL,
                        "input": text_clipped,
                        "voice": global_config.tts_voice_uri,
                        "response_format": "mp3",
                        # getattr 防御：部署机 config.py 旧版没有 tts_speed 字段时不炸（回落 1.0）
                        "speed": getattr(global_config, "tts_speed", 1.0),
                    },
                    timeout=120,
                    # 强制直连：部署机若有 HTTP(S)_PROXY 环境变量，requests 会自动走代理，
                    # 代理对二进制大响应处理异常时会返回 200+空 body
                    proxies={"http": None, "https": None},
                )
                if resp.status_code != 200:
                    logger.error(f"[send_voice] 合成失败 HTTP {resp.status_code}: {resp.text[:200]}")
                elif not resp.content:
                    _fp = f"{_key[:8]}...{_key[-4:]}" if _key else "(空key)"
                    logger.warning(
                        f"[send_voice] 第{attempt}次返回 200 空响应体（Content-Type: {resp.headers.get('Content-Type')}，"
                        f"key指纹: {_fp}）——硅基偶发抽风，重试"
                    )
                else:
                    mp3_bytes = resp.content
                    logger.info(f"[send_voice] 合成成功（{len(mp3_bytes)} 字节）")
                    break
            except Exception as e:
                logger.error(f"[send_voice] 第{attempt}次合成异常: {e}")
            if attempt < 3 and not mp3_bytes:
                await asyncio.sleep(2)
        if not mp3_bytes:
            return {"status": "failed", "message": "语音合成失败了（试了三次都没成功）"}

        # 2. 留档
        os.makedirs(_SAVE_DIR, exist_ok=True)
        fname = f"voice_{int(time.time())}_{hashlib.md5(text_clipped.encode()).hexdigest()[:8]}.mp3"
        fpath = os.path.join(_SAVE_DIR, fname)
        try:
            with open(fpath, "wb") as f:
                f.write(mp3_bytes)
        except OSError as e:
            logger.warning(f"语音文件留档失败: {e}")

        # 3. 发送到当前聊天（voice 段，base64）
        try:
            from ..tool_context import get_chat_id
            from ...plugins.chat.chat_stream import chat_manager
            from ...plugins.chat.message import MessageSending
            from ...plugins.message.api import global_api
            from ...plugins.storage.storage import MessageStorage
            from maim_message import UserInfo, Seg

            # 优先显式传参（PFC/统一大脑塞进 function_args 的），回落上下文
            chat_id = function_args.get("chat_id") or get_chat_id()
            logger.info(f"[send_voice] 发送环节: chat_id={chat_id}")
            chat_stream = chat_manager.get_stream(chat_id) if chat_id else None
            if not chat_stream:
                logger.error(
                    f"[send_voice] 拿不到当前聊天上下文（chat_id={chat_id}，类型 {type(chat_id).__name__}）——"
                    "PFC/统一大脑执行工具前未注入 current_chat_id，或该 stream 未在 chat_manager 注册"
                )
                return {"status": "failed", "message": "拿不到当前聊天上下文，语音没发出去"}

            b64 = base64.b64encode(mp3_bytes).decode()
            bot_user_info = UserInfo(
                user_id=global_config.BOT_QQ,
                user_nickname=global_config.BOT_NICKNAME,
                platform=chat_stream.platform,
            )
            message = MessageSending(
                message_id=f"voicetxt{round(time.time(), 2)}",
                chat_stream=chat_stream,
                bot_user_info=bot_user_info,
                sender_info=None,
                message_segment=Seg(type="voice", data=b64),
                reply=None,
                is_head=False,
                is_emoji=False,
            )
            # 不走 process()（voice 段会被转成"[未知消息类型]"），手动给入库文本——
            # 这样她回看上下文/记忆提炼时知道自己说了什么
            message.processed_plain_text = f"[语音] {text_clipped[:60]}"
            message.detailed_plain_text = f"[语音] {text_clipped[:60]}"
            await global_api.send_message(message)
            await MessageStorage().store_message(message, chat_stream)
        except Exception as e:
            logger.error(f"发送语音消息失败: {e}")
            return {"status": "failed", "message": "语音生成好了但发送失败"}

        logger.info(f"[send_voice] 语音已发送: {text_clipped[:50]}")
        return {"status": "ok", "message": f"语音发出去了（说的：{text_clipped[:40]}）"}
