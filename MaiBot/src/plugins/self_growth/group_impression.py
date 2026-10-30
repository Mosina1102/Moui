"""方案K层2：群像认知存储（每群一份"当前认知快照"）。

设计要点（v2.2）：
- my_attitude 是**行为参数不是情绪参数**（挖坑⑧）：注入"在这个群怎么表现"的相处策略，
  不注入"我不喜欢这个群"式情绪句——情绪归好感度系统（对人）管，避免双信号打架。
- 反自证回路防线（v2.2 新坑B）：变档需连续两日同向证据（pending 机制）；
  审查侧另行禁止摘她自己的发言作证据（见 group_reviewer）。
- 全部变更走 self_memory.write_journal 审计（铁律2）；入库口过敏感词（铁律1）。
- 存储为条目级小 JSON（每群一条快照），与 self_memory 同居 data/self_memory/。
"""
import json
import os
import threading
import time
from typing import Any, Dict, Optional

from src.common.logger_manager import get_logger

logger = get_logger("group_impression")

STORE_DIR = os.path.join("data", "self_memory")
STORE = os.path.join(STORE_DIR, "group_impressions.json")

_lock = threading.Lock()

# my_attitude 语义：行为参数（相处策略），不是情绪参数（挖坑⑧裁决）
_ATTITUDE_STRATEGY = {
    1: "这个群我一般潜水看戏，很少说话，偶尔冒个泡",
    2: "这个群我正常参与，不冷不热，有梗才接",
    3: "这个群我挺爱逛的，常参与话题",
    4: "这个群我爱掺和，话题来了喜欢插两句",
    5: "这个群是我的主场，说话放松随意，梗随便玩",
}

# 敏感词拦截（铁律1）。优先复用 reviewer 的正则；import 失败用最小集兜底
try:
    from .reviewer import SENSITIVE_PATTERN
except Exception:  # pragma: no cover
    import re as _re

    SENSITIVE_PATTERN = _re.compile(r"(密码|password|token|api[_ ]?key|secret|身份证|银行卡|卡号)", _re.IGNORECASE)

_ATTITUDE_LIMIT_SECONDS = 86400  # 变档 24h 限速（第二道闸，第一道是两日同向 pending）
_MAX_PENDING_DAYS_GAP = 1  # pending 日期必须是"昨天"才算同向连续


def _load_all_unlocked() -> Dict[str, Any]:
    """调用方必须已持有 _lock。"""
    try:
        with open(STORE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.error(f"读取失败: {e}")
        return {}


def _save_all_unlocked(data: Dict[str, Any]) -> None:
    """调用方必须已持有 _lock。"""
    os.makedirs(STORE_DIR, exist_ok=True)
    with open(STORE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_impression(stream_id: str) -> Optional[Dict[str, Any]]:
    if not stream_id:
        return None
    try:
        with _lock:
            return _load_all_unlocked().get(stream_id)
    except Exception as e:
        logger.error(f"get_impression 异常: {e}")
        return None


def get_all() -> Dict[str, Any]:
    """全量只读（监控看板用）。"""
    try:
        with _lock:
            return _load_all_unlocked()
    except Exception as e:
        logger.error(f"get_all 异常: {e}")
        return {}


def _default_impression(group_name: str) -> Dict[str, Any]:
    return {
        "group_name": group_name or "",
        "group_type": "",
        "atmosphere": "",
        "my_attitude": 2,
        "topics": [],
        "quirks": [],
        "pending_attitude_change": None,
        "evidence": "",
        "last_attitude_change": 0.0,
        "updated": 0.0,
        "anchor": 0.0,
    }


def _clamp_attitude(value, current: int) -> int:
    """单次最多变 1 档，clamp 到 [current-1, current+1] ∩ [1,5]（坑⑧/新坑B 双保险之一）"""
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        return current
    v = max(1, min(5, v))
    return max(current - 1, min(current + 1, v))


def apply_review(stream_id: str, group_name: str, updates: dict, evidence: str) -> bool:
    """审查器写入口。规则（施工方案 4.1 / v2.2）：
    ① evidence 空或"无变化" → 拒绝
    ② 敏感词拦截
    ③ my_attitude：两日同向 pending + 24h 限速 + 单日≤1档
    ⑤ 变更走 write_journal 审计
    ⑥ 刷新 updated/anchor
    返回是否有实际变更。
    """
    try:
        evidence = str(evidence or "").strip()
        if not evidence or evidence == "无变化":
            return False
        updates = updates or {}
        blob = json.dumps(updates, ensure_ascii=False, default=str) + evidence
        if SENSITIVE_PATTERN.search(blob):
            logger.warning(f"含敏感信息，拒绝更新: {stream_id}")
            return False

        changed_fields = []
        now = time.time()
        today = time.strftime("%m-%d", now)
        yesterday = time.strftime("%m-%d", time.localtime(now - 86400))

        with _lock:
            data = _load_all_unlocked()
            imp = data.get(stream_id)
            if not isinstance(imp, dict):
                imp = _default_impression(group_name)
                data[stream_id] = imp
            current = int(imp.get("my_attitude", 2) or 2)

            # ---- 普通字段 ----
            for key, cap in (("group_type", 20), ("atmosphere", 120)):
                v = str(updates.get(key) or "").strip()
                if v and v[:cap] != imp.get(key):
                    imp[key] = v[:cap]
                    changed_fields.append(key)

            for key, cap in (("topics", 20), ("quirks", 40)):
                items = updates.get(key)
                if isinstance(items, list):
                    cleaned = [str(x).strip()[:cap] for x in items if str(x).strip()][:5]
                    if cleaned and cleaned != imp.get(key):
                        imp[key] = cleaned
                        changed_fields.append(key)

            # ---- my_attitude：两日同向 pending + 限速 + 单日≤1档（新坑B）----
            try:
                proposed = int(float(updates.get("my_attitude")))
            except (TypeError, ValueError):
                proposed = current
            proposed = max(1, min(5, proposed))

            if proposed == current:
                # 证据不再支持旧的变档方向 → 清挂起
                if imp.get("pending_attitude_change"):
                    imp["pending_attitude_change"] = None
            else:
                pending = imp.get("pending_attitude_change") or {}
                same_direction = (
                    isinstance(pending, dict)
                    and int(pending.get("target", 0) or 0) == proposed
                    and str(pending.get("date", "")) == yesterday
                )
                if same_direction:
                    if now - float(imp.get("last_attitude_change", 0) or 0) >= _ATTITUDE_LIMIT_SECONDS:
                        new_attitude = _clamp_attitude(proposed, current)
                        if new_attitude != current:
                            imp["my_attitude"] = new_attitude
                            imp["last_attitude_change"] = now
                            changed_fields.append(f"my_attitude({current}->{new_attitude})")
                    else:
                        logger.info(f"态度变档被24h限速拦截: {stream_id}")
                    imp["pending_attitude_change"] = None
                else:
                    imp["pending_attitude_change"] = {
                        "target": proposed,
                        "evidence": evidence[:80],
                        "date": today,
                    }
                    logger.info(
                        f"态度变档挂起（待明日同向证据）: {stream_id} {current}->{proposed}"
                    )

            imp["evidence"] = evidence[:120]
            imp["updated"] = now
            imp["anchor"] = now
            if group_name and not imp.get("group_name"):
                imp["group_name"] = group_name
            _save_all_unlocked(data)

        if changed_fields:
            try:
                from .self_memory import write_journal

                write_journal(
                    "group_update",
                    {
                        "stream_id": stream_id,
                        "group_name": group_name,
                        "fields": changed_fields,
                        "evidence": evidence[:80],
                    },
                )
            except Exception as e:
                logger.error(f"写 journal 失败: {e}")
            logger.info(f"更新 {group_name or stream_id}: {changed_fields}")
            return True
        return False
    except Exception as e:
        logger.error(f"apply_review 异常: {e}")
        return False


def build_prompt_text(stream_id: str) -> str:
    """构建注入 prompt 的群像文本（分钟级稳定区）。无记录返回 ""。"""
    try:
        imp = get_impression(stream_id)
        if not imp:
            return ""
        try:
            att = int(imp.get("my_attitude", 2) or 2)
        except (TypeError, ValueError):
            att = 2
        strategy = _ATTITUDE_STRATEGY.get(att, _ATTITUDE_STRATEGY[2])
        lines = ["【我对这个群的印象】"]
        if imp.get("group_type"):
            lines.append(f"类型：{imp['group_type']}")
        if imp.get("atmosphere"):
            lines.append(f"氛围：{imp['atmosphere']}")
        lines.append(f"在这里我的状态：{strategy}")
        if imp.get("topics"):
            lines.append("这群常聊：" + "、".join(imp["topics"]))
        if imp.get("quirks"):
            lines.append("相处注意：" + "；".join(imp["quirks"]))
        return "\n" + "\n".join(lines) + "\n"
    except Exception as e:
        logger.error(f"build_prompt_text 异常: {e}")
        return ""


_MANUAL_FIELDS = {"group_type", "atmosphere", "my_attitude", "topics", "quirks"}


def set_field(stream_id: str, field: str, value) -> bool:
    """人工修正单个字段（监控看板操作）。绕过证据门槛但必须留 journal（op="manual_edit"）。
    仅允许白名单字段；my_attitude clamp 到 1-5；topics/quirks 必须是列表。"""
    if field not in _MANUAL_FIELDS or not stream_id:
        return False
    try:
        with _lock:
            data = _load_all_unlocked()
            imp = data.get(stream_id)
            if not isinstance(imp, dict):
                return False
            if field == "my_attitude":
                v = max(1, min(5, int(float(value))))
            elif field in ("topics", "quirks"):
                if not isinstance(value, list):
                    return False
                cap = 20 if field == "topics" else 40
                v = [str(x).strip()[:cap] for x in value if str(x).strip()][:5]
            else:
                v = str(value).strip()[:120]
                if not v:
                    return False
            imp[field] = v
            imp["updated"] = time.time()
            _save_all_unlocked(data)
        try:
            from .self_memory import write_journal

            write_journal("manual_edit", {"stream_id": stream_id, "field": field, "value": str(value)[:100]})
        except Exception:
            pass
        logger.info(f"人工修正 {stream_id}.{field}")
        return True
    except (TypeError, ValueError) as e:
        logger.error(f"人工修正字段值非法: {e}")
        return False
    except Exception as e:
        logger.error(f"set_field 异常: {e}")
        return False
