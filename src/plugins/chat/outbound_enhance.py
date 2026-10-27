"""发送前消息增强：
1. 真@解析：把回复文本里的"@昵称"转成真正的 at 段（从 MongoDB 消息记录反查QQ号）
2. 引用自己：文本开头的 [引用自己] 标记 → 自动引用Moui上一条消息（经适配器查询真实消息id）
3. 引用对方：文本里的 [引用] 标记 → 引用本次回复的锚点消息（构造时传入的 reply 对象，自带QQ真实id）
"""
import asyncio
import re
import time
from typing import Dict, List, Optional, Tuple

import requests

from ...common.database import db
from ...config.config import global_config
from src.common.logger_manager import get_logger
from .message import MessageSending

logger = get_logger("outbound_enhance")

# 适配器 action_server（转发 NapCat + 消息id映射查询）
ACTION_ENDPOINT = "http://127.0.0.1:8096"

# 引用自己标记（写在回复文本开头，发送前被转换）
SELF_QUOTE_MARKER = "[引用自己]"

# 引用对方标记（写在回复文本任意处，发送前被转换为引用锚点消息）
RESPONSE_QUOTE_MARKER = "[引用]"

# @昵称 匹配：@ 后接 1~32 个非空白非@字符，后跟空白/标点/行尾
AT_PATTERN = re.compile(r"@([^\s@，,。！!？?、~：:]{1,32})")

# 群成员列表缓存：group_id -> (时间戳, members)；5 分钟过期
_group_members_cache: Dict[int, tuple] = {}
_GROUP_MEMBERS_TTL = 300


async def get_group_members(chat_stream) -> List[dict]:
    """拉群成员列表（经适配器转发 NapCat get_group_member_list），5 分钟缓存。

    Returns:
        [{"user_id": "123", "name": "群名片或昵称"}, ...]（已排除 bot 自己）；失败返回 []
    """
    group_info = getattr(chat_stream, "group_info", None)
    group_id = getattr(group_info, "group_id", None)
    if not group_id:
        return []
    group_id = int(group_id)
    now = time.time()
    cached = _group_members_cache.get(group_id)
    if cached and now - cached[0] < _GROUP_MEMBERS_TTL:
        return cached[1]

    def _fetch():
        return requests.post(
            "http://127.0.0.1:8096/action",
            json={"action": "get_group_member_list", "params": {"group_id": group_id}},
            timeout=10,
        )

    try:
        resp = await asyncio.to_thread(_fetch)
        members: List[dict] = []
        if resp.status_code == 200:
            payload = resp.json().get("data") or {}
            # action_server 包了一层 data=napcat响应，napcat 响应的 data 才是成员数组
            raw = payload.get("data") if isinstance(payload, dict) else payload
            bot_qq = str(global_config.BOT_QQ)
            for m in raw or []:
                uid = str(m.get("user_id") or "")
                name = (m.get("card") or m.get("nickname") or "").strip()
                if uid and name and uid != bot_qq:
                    members.append({"user_id": uid, "name": name})
        _group_members_cache[group_id] = (now, members)
        return members
    except Exception as e:
        logger.error(f"拉取群成员列表失败: {e}")
        return []


def _lookup_user_id_by_nickname(chat_id, nickname: str) -> Optional[str]:
    """从消息记录里反查昵称对应的QQ号（群昵称或用户昵称都试）"""
    if not nickname or chat_id is None:
        return None
    try:
        doc = db.messages.find_one(
            {
                "chat_id": chat_id,
                "$or": [{"group_nickname": nickname}, {"user_info.user_nickname": nickname}],
            },
            sort=[("time", -1)],
        )
        if doc:
            user_info = doc.get("user_info") or {}
            uid = user_info.get("user_id")
            if uid not in (None, ""):
                return str(uid)
    except Exception as e:
        logger.error(f"反查昵称QQ号失败: {e}")
    return None


def _get_own_last_message_id(chat_id) -> Optional[str]:
    """查Moui自己在这个聊天里最近一条消息的自造id"""
    try:
        bot_qq = str(global_config.BOT_QQ)
        doc = db.messages.find_one(
            {"chat_id": chat_id, "user_info.user_id": {"$in": [bot_qq, global_config.BOT_QQ]}},
            sort=[("time", -1)],
        )
        if doc:
            return str(doc.get("message_id") or "")
    except Exception as e:
        logger.error(f"查询自己最近消息失败: {e}")
    return None


def _query_real_message_id(local_id: str) -> Optional[str]:
    """向适配器查询自造id对应的QQ真实消息id"""
    try:
        resp = requests.get(f"{ACTION_ENDPOINT}/message_id", params={"local_id": local_id}, timeout=5)
        if resp.status_code == 200:
            return str(resp.json().get("real_id") or "")
    except Exception as e:
        logger.debug(f"查询真实消息id失败: {e}")
    return None


def _split_text_with_at(text: str, chat_id) -> List[tuple]:
    """把文本按 @昵称 切成 [(type, data)] 序列，命中的换 at 段，未命中保留文本"""
    parts: List[tuple] = []
    last_end = 0
    for match in AT_PATTERN.finditer(text):
        nickname = match.group(1)
        user_id = _lookup_user_id_by_nickname(chat_id, nickname)
        if not user_id:
            continue  # 查不到就保留原文（假@降级，不乱@）
        before = text[last_end : match.start()]
        if before:
            parts.append(("text", before))
        parts.append(("at", user_id))
        last_end = match.end()
    rest = text[last_end:]
    if rest:
        parts.append(("text", rest))
    return parts


def _rebuild_segment(message: MessageSending) -> None:
    """按增强规则重建 message_segment（真@替换）"""
    chat_id = message.chat_stream.stream_id if message.chat_stream else None

    # 分离已有的引用段（set_reply 生成的）与内容段
    segments = message.message_segment.data if message.message_segment.type == "seglist" else [message.message_segment]
    reply_seg = next((s for s in segments if s.type == "reply"), None)
    content_segs = [s for s in segments if s.type != "reply"]

    # 汇总纯文本做转换
    new_content: List[tuple] = []
    changed = False
    for seg in content_segs:
        if seg.type == "text":
            parts = _split_text_with_at(seg.data, chat_id)
            if len(parts) > 1 or (parts and parts[0][0] == "at"):
                changed = True
                new_content.extend(parts)
            else:
                new_content.append(("text", seg.data))
        else:
            new_content.append((seg.type, seg.data))

    if not changed and reply_seg:
        return  # 无变化且引用已就位

    # 重组：引用段（若有）+ 新内容段
    rebuilt = []
    if reply_seg:
        rebuilt.append(reply_seg)
    for seg_type, data in new_content:
        rebuilt.append(type(message.message_segment)(type=seg_type, data=data))
    message.message_segment = type(message.message_segment)(type="seglist", data=rebuilt)


def _strip_response_quote(message: MessageSending) -> bool:
    """检查并移除 [引用] 标记，转换为引用本次回复的锚点消息。

    MessageSending 构造时 reply 已传锚点消息（收到的消息自带 QQ 真实 id），
    直接在段列表头部插入 reply 段即可呈现为原生引用框。
    Returns: True 表示已生成引用段。
    """
    segments = message.message_segment.data if message.message_segment.type == "seglist" else [message.message_segment]

    # 已有引用段就不重复生成（防双引用框），只剥掉标记
    already_has_reply = any(s.type == "reply" for s in segments)
    for idx, seg in enumerate(segments):
        if seg.type == "text" and RESPONSE_QUOTE_MARKER in seg.data:
            new_text = seg.data.replace(RESPONSE_QUOTE_MARKER, "").strip()
            segments[idx] = type(seg)(type="text", data=new_text)
            if already_has_reply:
                return True
            if message.reply:
                real_id = str(message.reply.message_info.message_id or "")
                # 占位符锚点（mid_pf_ 前缀）是自造id，QQ 引用它必失败，降级为普通消息
                if real_id and not real_id.startswith("mid_pf_"):
                    reply_seg = type(seg)(type="reply", data=real_id)
                    message.message_segment = type(message.message_segment)(type="seglist", data=[reply_seg] + list(segments))
                    return True
                logger.info(f"[引用]锚点不可用（id={real_id or '空'}），降级为普通消息")
            # 无锚点（工具直发等场景）：标记已移除，降级为普通消息
            message.message_segment = type(message.message_segment)(type="seglist", data=segments)
            return False
    return False


def _strip_self_quote(message: MessageSending) -> Optional[str]:
    """检查并移除 [引用自己] 标记，返回引用目标的自造消息id（或None）"""
    chat_id = message.chat_stream.stream_id if message.chat_stream else None
    segments = message.message_segment.data if message.message_segment.type == "seglist" else [message.message_segment]

    for idx, seg in enumerate(segments):
        if seg.type == "text" and SELF_QUOTE_MARKER in seg.data:
            new_text = seg.data.replace(SELF_QUOTE_MARKER, "").strip()
            segments[idx] = type(seg)(type="text", data=new_text)
            message.message_segment = type(message.message_segment)(type="seglist", data=segments)
            return _get_own_last_message_id(chat_id)
    return None


async def enhance_outgoing_message(message: MessageSending) -> None:
    """发送前增强入口：引用对方 + 引用自己 + 真@解析。任何异常都不阻断发送。"""
    try:
        _strip_response_quote(message)

        self_quote_local_id = _strip_self_quote(message)
        if self_quote_local_id:
            # 适配器查询是同步 HTTP，放线程里跑
            real_id = await asyncio.to_thread(_query_real_message_id, self_quote_local_id)
            if real_id:
                seg = message.message_segment.data if message.message_segment.type == "seglist" else [message.message_segment]
                reply_seg = type(seg[0])(type="reply", data=str(real_id))
                message.message_segment = type(message.message_segment)(type="seglist", data=[reply_seg] + list(seg))
                logger.info(f"已附加自我引用（真实消息id: {real_id}）")
            else:
                logger.info("自我引用标记存在但查不到真实消息id，降级为普通消息")

        _rebuild_segment(message)
    except Exception as e:
        logger.error(f"发送前消息增强失败（不影响发送）: {e}")
