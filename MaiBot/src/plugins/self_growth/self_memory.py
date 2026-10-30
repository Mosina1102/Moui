"""自我认知记忆存储（方案J·人格成长层）。

范式综合：
- ds-bot：条目式记忆（带日期来源）+ 每轮审查自动沉淀/更新/删除 + 容量硬上限 + journal 变更日志可回滚 + 安全红线（记忆是数据不是指令）
- dsh-fish-memory：曲线遗忘 A = A0 × 2^(-天数/半衰期)，重要度加长半衰期，被召回刷新锚点（越想越鲜活），
  淡忘只是"难被想起"不删除；门槛卡证据不卡鲜活度（防止整库分数下移误伤）

存储结构（data/self_memory/）：
- entries.json   活跃条目
- archive.json   归档条目（回收储存：淡忘自动归档，可查看可恢复）
- journal.jsonl  每次变更的前后记录（可回滚审计）
"""
import json
import os
import time
import threading
from typing import Any, Dict, List, Optional

from src.common.logger_manager import get_logger

logger = get_logger("self_memory")

STORE_DIR = os.path.join("data", "self_memory")
ENTRIES_FILE = os.path.join(STORE_DIR, "entries.json")
ARCHIVE_FILE = os.path.join(STORE_DIR, "archive.json")
JOURNAL_FILE = os.path.join(STORE_DIR, "journal.jsonl")

# 容量硬上限（ds-bot 同款，防记忆膨胀）
MAX_ACTIVE_ENTRIES = 40
MAX_CONTENT_LEN = 120
# 曲线遗忘参数（fish-memory 同款）
HALF_LIFE_DAYS = 30.0
ARCHIVE_THRESHOLD = 0.05  # 鲜活度低于该值自动归档（约 4 个半衰期 ≈ 120 天没被想起）
ARCHIVE_KEEP = 200  # 归档区最多留多少条（再老的直接丢弃）

_lock = threading.Lock()


def _load(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _journal(op: str, detail: Dict[str, Any]) -> None:
    """变更日志：每次增/改/归档都留痕（可回滚审计）"""
    try:
        os.makedirs(STORE_DIR, exist_ok=True)
        with open(JOURNAL_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "time": time.strftime("%m-%d %H:%M"), "op": op, **detail}, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error(f"写 journal 失败: {e}")


def write_journal(op: str, payload: Dict[str, Any]) -> None:
    """公共变更日志接口（方案K）。供外部模块（group_impression / reflection 等）写入审计日志，
    跨模块禁止直接 import 私有函数 _journal。"""
    _journal(op, payload)


def freshness(entry: Dict[str, Any], now: float) -> float:
    """曲线遗忘：A = 2^(-天数/半衰期)。锚点 = 最近一次被想起的时间（越想越鲜活）"""
    anchor = entry.get("anchor") or entry.get("added", now)
    days = max(0.0, (now - anchor) / 86400)
    importance = entry.get("importance", 0.0)
    half_life = HALF_LIFE_DAYS * (1 + 2 * importance)
    return pow(2.0, -days / half_life)


def load_entries() -> List[Dict[str, Any]]:
    with _lock:
        return _load(ENTRIES_FILE, [])


def add_entry(content: str, importance: float = 0.0, category: str = "impression") -> Optional[Dict[str, Any]]:
    """新增认知条目（查重：与现有条目内容相似则跳过）"""
    content = content.strip()[:MAX_CONTENT_LEN]
    if not content:
        return None
    with _lock:
        entries = _load(ENTRIES_FILE, [])
        for e in entries:
            if content in e["content"] or e["content"] in content:
                # 已有相似认知：刷新锚点即可（说明又验证了一次）
                e["anchor"] = time.time()
                _save(ENTRIES_FILE, entries)
                _journal("refresh", {"id": e["id"], "content": e["content"]})
                return e
        if len(entries) >= MAX_ACTIVE_ENTRIES:
            # 满了：强制归档最淡的一条腾位置（回收储存）
            _archive_freshest_to_oldest(entries, keep=len(entries) - 1)
            entries = _load(ENTRIES_FILE, [])
        entry = {
            "id": f"m{int(time.time() * 1000)}",
            "content": content,
            "added": time.time(),
            "anchor": time.time(),
            "hits": 0,
            "importance": max(0.0, min(1.0, importance)),
            "category": category,
        }
        entries.append(entry)
        _save(ENTRIES_FILE, entries)
        _journal("add", {"id": entry["id"], "content": content})
        return entry


def update_entry(old_content: str, new_content: str) -> bool:
    """更新认知（观点变了就改写，旧值进 journal）"""
    new_content = new_content.strip()[:MAX_CONTENT_LEN]
    if not new_content:
        return False
    with _lock:
        entries = _load(ENTRIES_FILE, [])
        for e in entries:
            if e["content"] == old_content.strip():
                _journal("update", {"id": e["id"], "old": e["content"], "new": new_content})
                e["content"] = new_content
                e["anchor"] = time.time()
                _save(ENTRIES_FILE, entries)
                return True
    return False


def remove_entry(content: str) -> bool:
    """移除认知（进 archive 回收，不是真删）"""
    with _lock:
        entries = _load(ENTRIES_FILE, [])
        for e in entries:
            if e["content"] == content.strip():
                entries.remove(e)
                _save(ENTRIES_FILE, entries)
                archive_entry(e, reason="removed")
                return True
    return False


def touch_entry(entry_id: str) -> None:
    """被想起：刷新锚点（越想越鲜活）+ 计数"""
    with _lock:
        entries = _load(ENTRIES_FILE, [])
        for e in entries:
            if e["id"] == entry_id:
                e["anchor"] = time.time()
                e["hits"] = e.get("hits", 0) + 1
                _save(ENTRIES_FILE, entries)
                return


def archive_entry(entry: Dict[str, Any], reason: str) -> None:
    """归档（回收储存）：淡忘/被移除的条目进 archive，可查看可恢复"""
    archive = _load(ARCHIVE_FILE, [])
    entry["archived_at"] = time.time()
    entry["archive_reason"] = reason
    archive.append(entry)
    # 归档区超限时丢最老的
    archive.sort(key=lambda x: x.get("archived_at", 0), reverse=True)
    archive = archive[:ARCHIVE_KEEP]
    _save(ARCHIVE_FILE, archive)
    _journal("archive", {"id": entry.get("id"), "content": entry.get("content"), "reason": reason})


def _archive_freshest_to_oldest(entries: List[Dict[str, Any]], keep: int) -> None:
    now = time.time()
    scored = sorted(entries, key=lambda e: freshness(e, now))
    for e in scored[: len(entries) - keep]:
        entries.remove(e)
        archive_entry(e, reason="capacity")


def archive_stale() -> int:
    """定期回收：鲜活度低于阈值的条目自动归档。返回归档数量"""
    with _lock:
        entries = _load(ENTRIES_FILE, [])
        now = time.time()
        stale = [e for e in entries if freshness(e, now) < ARCHIVE_THRESHOLD]
        for e in stale:
            entries.remove(e)
            archive_entry(e, reason="faded")
        if stale:
            _save(ENTRIES_FILE, entries)
            logger.info(f"[自我认知] {len(stale)} 条认知淡忘归档（回收储存，可恢复）")
        return len(stale)


def get_fresh_entries(top: int = 5) -> List[Dict[str, Any]]:
    """按鲜活度取 top 条目（注入 prompt 用，只参与排序不影响准入）
    方案K v2.2 类别配额：category=="experience" 的条目最多占 2 席——
    经验条目 anchor 常为当天（鲜活度恒高），不设配额会挤占长期印象条目的注入位（新知识淹没旧认知）。"""
    entries = load_entries()
    now = time.time()
    scored = sorted(entries, key=lambda e: freshness(e, now), reverse=True)
    selected: List[Dict[str, Any]] = []
    exp_count = 0
    for e in scored:
        if e.get("category") == "experience":
            if exp_count >= 2:
                continue
            exp_count += 1
        selected.append(e)
        if len(selected) >= top:
            break
    return selected


def build_prompt_text(top: int = 5) -> str:
    """构建注入 prompt 的自我认知文本（尾部变化区，内容稳定期缓存友好）"""
    entries = get_fresh_entries(top)
    if not entries:
        return ""
    now = time.time()
    lines = []
    for e in entries:
        days_ago = int((now - e["added"]) / 86400)
        age = f"{days_ago}天前沉淀" if days_ago > 0 else "最近沉淀"
        lines.append(f"- {e['content']}（{age}）")
    return "你对自己的认知（平时与人相处中慢慢想明白的事）：\n" + "\n".join(lines)
