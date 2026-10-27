import asyncio
import json
import os
import time
from typing import Any, Dict, Tuple

import requests

from src.common.logger_manager import get_logger

logger = get_logger("account_action")

# 适配器账号操作端点（MaiBot-Napcat-Adapter 的 action_server，只绑本机）
ACTION_ENDPOINT = os.getenv("NAPCAT_ACTION_ENDPOINT", "http://127.0.0.1:8096/action")

# 冷却记录文件（每工具记录上次使用时间戳）
COOLDOWN_FILE = os.path.join("data", "account_action_cooldown.json")

# 各工具冷却时长（小时）；放宽版——防止抽风循环即可，不硬卡人设表达欲
COOLDOWN_HOURS = {
    "change_avatar": 6,
    "change_signature": 6,
    "change_nickname": 24,
    "send_poke": 1,
}

# 允许读取头像图片的白名单目录（相对 MaiBot 根目录）
ALLOWED_IMAGE_DIRS = ["emoji", "data/images"]


async def call_napcat_action(action: str, params: Dict[str, Any], timeout: int = 20) -> Dict[str, Any]:
    """通过适配器 action HTTP 端点调用 NapCat 扩展接口"""

    def _post():
        return requests.post(ACTION_ENDPOINT, json={"action": action, "params": params}, timeout=timeout)

    try:
        resp = await asyncio.to_thread(_post)
    except Exception as e:
        logger.error(f"调用 {action} 失败（适配器不可达）: {e}")
        return {"status": "failed", "message": f"适配器不可达（{e}），请稍后再试"}

    try:
        data = resp.json()
    except Exception:
        return {"status": "failed", "message": f"适配器响应异常: HTTP {resp.status_code}"}

    if resp.status_code != 200:
        return {"status": "failed", "message": data.get("message", f"HTTP {resp.status_code}")}

    napcat_resp = data.get("data") or {}
    if isinstance(napcat_resp, dict) and napcat_resp.get("status") != "ok":
        msg = napcat_resp.get("wording") or napcat_resp.get("message") or str(napcat_resp)
        return {"status": "failed", "message": f"NapCat 返回失败: {msg}"}
    return {"status": "ok", "data": napcat_resp}


def _load_cooldowns() -> Dict[str, float]:
    try:
        with open(COOLDOWN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def check_cooldown(tool_name: str) -> Tuple[bool, float]:
    """检查冷却状态。返回 (是否允许使用, 剩余小时数)"""
    hours = COOLDOWN_HOURS.get(tool_name, 24)
    last = _load_cooldowns().get(tool_name, 0)
    remain = hours - (time.time() - last) / 3600
    if remain > 0:
        return False, remain
    return True, 0


def set_cooldown(tool_name: str) -> None:
    cooldowns = _load_cooldowns()
    cooldowns[tool_name] = time.time()
    os.makedirs(os.path.dirname(COOLDOWN_FILE), exist_ok=True)
    try:
        with open(COOLDOWN_FILE, "w", encoding="utf-8") as f:
            json.dump(cooldowns, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"写入冷却记录失败: {e}")


def record_action(tool_name: str, detail: str) -> None:
    logger.info(f"[{tool_name}] {detail}")
