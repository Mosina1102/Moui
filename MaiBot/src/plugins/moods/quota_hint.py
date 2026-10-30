"""社交账号配额提示：让模型看到自己当前还能发几条空间/多久能再改头像签名昵称。

注入点：MoodManager.get_prompt() 尾部（PFC 私聊与群聊统一大脑都会注入）。
配额数据来自工具层的冷却记录文件（data/qzone_cooldown.json / data/account_action_cooldown.json）。
"""
import json
import os
import time
from typing import Dict

from src.common.logger_manager import get_logger

logger = get_logger("quota_hint")

QZONE_COOLDOWN_FILE = os.path.join("data", "qzone_cooldown.json")
ACCOUNT_COOLDOWN_FILE = os.path.join("data", "account_action_cooldown.json")
QZONE_COMMENT_COOLDOWN_FILE = os.path.join("data", "qzone_comment_cooldown.json")
QZONE_DAILY_LIMIT = 3
QZONE_COMMENT_DAILY_LIMIT = 20

# 账号工具冷却时长（与 account_action_common.COOLDOWN_HOURS 保持一致）
ACCOUNT_COOLDOWN_HOURS = {
    "change_avatar": ("换头像", 6),
    "change_signature": ("改签名", 6),
    "change_nickname": ("改昵称", 24),
}


def _load_json(path: str) -> Dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def build_quota_hint() -> str:
    """生成配额提示文本。无任何可用信息时返回空串（不占 prompt）。"""
    parts = []

    # 空间：今日剩余条数
    qzone = _load_json(QZONE_COOLDOWN_FILE)
    today = time.strftime("%Y-%m-%d")
    if qzone.get("date") == today:
        used = int(qzone.get("count", 0) or 0)
    else:
        used = 0
    remain_qzone = max(0, QZONE_DAILY_LIMIT - used)
    parts.append(f"今天空间已发 {used}/{QZONE_DAILY_LIMIT} 条" + ("（今日额度用完，别再提发空间了）" if remain_qzone == 0 else ""))

    # 空间评论：今日剩余条数
    qz_comment = _load_json(QZONE_COMMENT_COOLDOWN_FILE)
    if qz_comment.get("date") == today:
        c_used = int(qz_comment.get("count", 0) or 0)
    else:
        c_used = 0
    parts.append(f"今天空间评论 {c_used}/{QZONE_COMMENT_DAILY_LIMIT} 条")

    # 账号操作：剩余冷却
    cooldowns = _load_json(ACCOUNT_COOLDOWN_FILE)
    now = time.time()
    for tool, (label, hours) in ACCOUNT_COOLDOWN_HOURS.items():
        last = float(cooldowns.get(tool, 0) or 0)
        remain = hours - (now - last) / 3600
        if remain > 0:
            parts.append(f"{label}还要 {remain:.0f} 小时才能再改")
        else:
            parts.append(f"{label}随时可改")

    if not parts:
        return ""
    return "（你的社交账号状态：" + "；".join(parts) + "）"
