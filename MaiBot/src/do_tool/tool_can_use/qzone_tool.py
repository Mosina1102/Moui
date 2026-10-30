import asyncio
import json
import os
import time
from typing import Any, Dict, Tuple

from src.common.logger_manager import get_logger
from .base_tool import BaseTool

logger = get_logger("qzone_tool")

# 空间冷却：每天最多 3 条（空间风控铁律），条与条之间不设硬间隔——由总数限制兜底
DAILY_LIMIT = 3
COOLDOWN_FILE = os.path.join("data", "qzone_cooldown.json")


def _check_cooldown() -> Tuple[bool, str]:
    """返回 (是否允许, 说明)"""
    try:
        with open(COOLDOWN_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}

    today = time.strftime("%Y-%m-%d")
    if state.get("date") != today:
        state = {"date": today, "count": 0, "last_ts": 0}

    if state.get("count", 0) >= DAILY_LIMIT:
        return False, f"今天已经发过 {DAILY_LIMIT} 条空间了，一天别发太多"
    return True, ""


def _set_cooldown() -> None:
    today = time.strftime("%Y-%m-%d")
    try:
        with open(COOLDOWN_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        if state.get("date") != today:
            state = {"date": today, "count": 0, "last_ts": 0}
    except (FileNotFoundError, json.JSONDecodeError):
        state = {"date": today, "count": 0, "last_ts": 0}
    state["count"] = state.get("count", 0) + 1
    state["last_ts"] = time.time()
    os.makedirs(os.path.dirname(COOLDOWN_FILE), exist_ok=True)
    try:
        with open(COOLDOWN_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"写入空间冷却记录失败: {e}")


# 评论风控：60 秒间隔 + 每天 20 条上限（独立记账，不占发说说的 3 条额度）
COMMENT_COOLDOWN_SECONDS = 60
COMMENT_DAILY_LIMIT = 20
COMMENT_COOLDOWN_FILE = os.path.join("data", "qzone_comment_cooldown.json")


def _check_comment_cooldown() -> Tuple[bool, str]:
    try:
        with open(COMMENT_COOLDOWN_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    today = time.strftime("%Y-%m-%d")
    if state.get("date") != today:
        state = {"date": today, "count": 0, "last_ts": 0}
    if state.get("count", 0) >= COMMENT_DAILY_LIMIT:
        return False, f"今天已经评论 {COMMENT_DAILY_LIMIT} 条了，别刷屏"
    elapsed = time.time() - state.get("last_ts", 0)
    if elapsed < COMMENT_COOLDOWN_SECONDS:
        return False, f"刚评论过，歇 {COMMENT_COOLDOWN_SECONDS - elapsed:.0f} 秒再说"
    return True, ""


def _set_comment_cooldown() -> None:
    today = time.strftime("%Y-%m-%d")
    try:
        with open(COMMENT_COOLDOWN_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        if state.get("date") != today:
            state = {"date": today, "count": 0, "last_ts": 0}
    except (FileNotFoundError, json.JSONDecodeError):
        state = {"date": today, "count": 0, "last_ts": 0}
    state["count"] = state.get("count", 0) + 1
    state["last_ts"] = time.time()
    os.makedirs(os.path.dirname(COMMENT_COOLDOWN_FILE), exist_ok=True)
    try:
        with open(COMMENT_COOLDOWN_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"写入空间评论冷却记录失败: {e}")


def _run_post(content: str, image_path: str = "") -> Dict[str, Any]:
    from ...config.config import global_config
    from ...plugins.qzone.qzone_client import get_qzone_client

    client, err = get_qzone_client(bot_qq=str(global_config.BOT_QQ))
    if not client:
        return {"status": "failed", "message": err}
    return client.publish_mood(content, image_path=image_path)


def _run_comment(owner_uin: str, tid: str, content: str, comment_id: str = "", appid: str = "311") -> Dict[str, Any]:
    from ...config.config import global_config
    from ...plugins.qzone.qzone_client import get_qzone_client

    client, err = get_qzone_client(bot_qq=str(global_config.BOT_QQ))
    if not client:
        return {"status": "failed", "message": err}
    return client.comment_feed(owner_uin, tid, content, comment_id=comment_id, appid=appid)


# 图片来源白名单（同 change_avatar 的规则）：表情包目录 + 网络搜图缓存目录 + 聊天图片落地目录（save_pic 开启时）
_ALLOWED_IMAGE_DIRS = ("emoji", "data/emoji", "data/images", "data/images_web", "data/image")


def _run_list() -> Dict[str, Any]:
    from ...config.config import global_config
    from ...plugins.qzone.qzone_client import get_qzone_client

    client, err = get_qzone_client(bot_qq=str(global_config.BOT_QQ))
    if not client:
        return {"status": "failed", "message": err}
    return client.get_my_moods(5)


class PostQzoneTool(BaseTool):
    name = "post_qzone"
    description = (
        "发一条 QQ 空间说说，可带一张图。适合记录心情、分享感受、吐槽日常、发看到的好图——像真人一样偶尔发个空间。"
        "content 控制在200字以内，语气要像平时说话；image_path 可选，填允许目录下的图片相对路径"
        "（如 emoji/xxx.webp 或 data/images_web/xxx.jpg），带图时配一句短文案效果最好。"
        "每天最多发3条（自己的账号余额状态会提示你），不要刷屏。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "说说内容，200字以内"},
            "image_path": {
                "type": "string",
                "description": "可选。要带的图片相对路径（emoji/ 或 data/images、data/images_web 目录内）",
            },
        },
        "required": ["content"],
    }

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        content = (function_args.get("content") or "").strip()
        image_path = str(function_args.get("image_path") or "").strip()
        if not content:
            return {"status": "failed", "message": "content 不能为空"}
        if len(content) > 200:
            return {"status": "failed", "message": f"内容太长了（{len(content)}字），空间说说控制在200字以内"}

        if image_path:
            norm = image_path.replace("\\", "/").lstrip("/")
            if not norm.startswith(_ALLOWED_IMAGE_DIRS):
                return {"status": "failed", "message": f"image_path 必须是这些目录下的图片: {', '.join(_ALLOWED_IMAGE_DIRS)}"}
            if not os.path.exists(norm):
                return {"status": "failed", "message": f"图片文件不存在: {norm}"}
            image_path = norm

        allowed, reason = _check_cooldown()
        if not allowed:
            return {"status": "failed", "message": reason}

        result = await asyncio.to_thread(_run_post, content, image_path)
        if result.get("success"):
            _set_cooldown()
            logger.info(f"[发空间] {'[带图] ' if image_path else ''}{content[:50]}")
            return {"status": "ok", "message": "空间说说发表成功" + ("（带图）" if image_path else "")}
        return {"status": "failed", "message": result.get("error", "发表失败")}


class CommentQzoneTool(BaseTool):
    name = "comment_qzone"
    description = (
        "评论别人的 QQ 空间说说，或回复说说下的某条评论（楼中楼）。"
        "owner_uin=说说主人的QQ号，tid=那条说说的id（浏览空间时 feed 信息里都有），"
        "content=评论内容（100字以内，像平时说话）。"
        "要回复某条具体评论时，把那条评论的 comment_id 一起填上。别刷屏，对同一条说说别连评。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "owner_uin": {"type": "string", "description": "说说主人的QQ号"},
            "tid": {"type": "string", "description": "说说id（浏览时 feed 信息里的 tid）"},
            "content": {"type": "string", "description": "评论内容，100字以内"},
            "comment_id": {
                "type": "string",
                "description": "可选。要回复的那条评论的 comment_id（回复别人的评论时填）",
            },
            "appid": {"type": "string", "description": "说说类型id，默认311（浏览时 feed 信息里有就原样传）"},
        },
        "required": ["owner_uin", "tid", "content"],
    }

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        owner_uin = str(function_args.get("owner_uin") or "").strip()
        tid = str(function_args.get("tid") or "").strip()
        content = str(function_args.get("content") or "").strip()
        comment_id = str(function_args.get("comment_id") or "").strip()
        appid = str(function_args.get("appid") or "311").strip() or "311"
        if not owner_uin or not tid or not content:
            return {"status": "failed", "message": "owner_uin / tid / content 都不能为空"}
        if len(content) > 100:
            return {"status": "failed", "message": f"评论太长了（{len(content)}字），控制在100字以内"}

        # 评论风控：60 秒冷却 + 每天上限（同目录独立记账，不占发说说的 3 条额度）
        allowed, reason = _check_comment_cooldown()
        if not allowed:
            return {"status": "failed", "message": reason}

        result = await asyncio.to_thread(_run_comment, owner_uin, tid, content, comment_id, appid)
        if result.get("success"):
            _set_comment_cooldown()
            target = f"回复评论({comment_id})" if comment_id else "评论"
            logger.info(f"[空间评论] {target} 说说{tid[:20]}: {content[:40]}")
            return {"status": "ok", "message": ("回复评论成功" if comment_id else "评论成功")}
        return {"status": "failed", "message": result.get("error", "评论失败")}


class ListQzoneTool(BaseTool):
    name = "list_qzone"
    description = "查看自己最近发的 QQ 空间说说（最多5条），用于回忆自己发过什么。"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        result = await asyncio.to_thread(_run_list)
        if not result.get("success"):
            return {"status": "failed", "message": result.get("error", "获取失败")}
        moods = result.get("moods") or []
        if not moods:
            return {"status": "ok", "message": "空间里还没有说说"}
        lines = [f"{m['time']}：{m['content']}" for m in moods]
        return {"status": "ok", "message": "最近的空间动态：\n" + "\n".join(lines)}
