"""方案K层1：审核打分流存储。

数据流：group_reply_checker.check_group_reply 每审核一条 text_reply 就 append_event 一行，
下游消费方（层2 plastic_scheduler 枚举活跃群 / 层3 reflection 每日复盘）走 get_recent / active_stream_ids。

锁纪律（挖坑报告坑④）：_lock 内只做 O(1) 追加 / 快照字节读出；解析、清理计算一律在锁外——
打分流落盘在审核返回路径上（同步调用），读操作持锁解析会卡住活跃群每条回复的审核。
"""
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from src.common.logger_manager import get_logger

logger = get_logger("score_events")

STORE_DIR = os.path.join("data", "self_memory")
STORE = os.path.join(STORE_DIR, "score_events.jsonl")

_lock = threading.Lock()


def append_event(stream_id: str, group_name: str, passed: bool, score: int, reason: str, reply: str) -> None:
    """追加一条打分记录。调用方：group_reply_checker.check_group_reply（每条 text_reply 审核后）。

    记录字段：ts/time/stream_id/group_name/passed/score(0-10 int)/reason/reply_head(120字)。
    锁内 O(1) 追加，任何异常不外抛（fail-open，审核链路不等它）。
    """
    try:
        score_val = max(0, min(10, int(float(score))))
    except (TypeError, ValueError):
        score_val = 5
    record = {
        "ts": time.time(),
        "time": time.strftime("%m-%d %H:%M:%S"),
        "stream_id": str(stream_id or ""),
        "group_name": str(group_name or ""),
        "passed": bool(passed),
        "score": score_val,
        "reason": str(reason or "")[:200],
        "reply_head": str(reply or "").strip()[:120],
    }
    line = json.dumps(record, ensure_ascii=False)
    try:
        with _lock:  # 锁内只做 O(1) 追加
            os.makedirs(STORE_DIR, exist_ok=True)
            with open(STORE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as e:
        logger.error(f"打分流写入失败: {e}")


def _load_snapshot() -> List[Dict[str, Any]]:
    """锁内读出全部原始行（快照），锁外解析。解析失败的单行跳过不抛。"""
    try:
        with _lock:
            with open(STORE, "r", encoding="utf-8") as f:
                raw_lines = f.readlines()
    except FileNotFoundError:
        return []
    except Exception as e:
        logger.error(f"打分流读取失败: {e}")
        return []

    records: List[Dict[str, Any]] = []
    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
            if isinstance(item, dict):
                records.append(item)
        except json.JSONDecodeError:
            continue
    return records


def get_recent(days: int = 1, stream_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """读取近 N 天记录（stream_id 为 None 时全群）。返回按 ts 升序的 dict 列表。"""
    cutoff = time.time() - max(0, days) * 86400
    records = [r for r in _load_snapshot() if float(r.get("ts", 0) or 0) >= cutoff]
    if stream_id is not None:
        records = [r for r in records if r.get("stream_id") == stream_id]
    records.sort(key=lambda r: float(r.get("ts", 0) or 0))
    return records


def active_stream_ids(days: int = 1) -> List[str]:
    """近 N 天有打分记录的 stream_id 去重列表。供层2调度与层3枚举"活跃群"。"""
    seen: Dict[str, None] = {}
    for r in _load_snapshot():
        if float(r.get("ts", 0) or 0) >= time.time() - max(0, days) * 86400:
            sid = r.get("stream_id") or ""
            if sid:
                seen[sid] = None
    return list(seen.keys())


def cleanup(retention_days: int = 30) -> int:
    """删除 ts 早于保留期的行，返回删除数。锁内读快照+写临时文件+os.replace 原子替换。"""
    cutoff = time.time() - max(0, retention_days) * 86400
    try:
        with _lock:
            if not os.path.exists(STORE):
                return 0
            with open(STORE, "r", encoding="utf-8") as f:
                raw_lines = f.readlines()
        # 锁外计算保留行
        kept: List[str] = []
        removed = 0
        for raw in raw_lines:
            raw_s = raw.strip()
            if not raw_s:
                continue
            try:
                ts = float(json.loads(raw_s).get("ts", 0) or 0)
            except (json.JSONDecodeError, ValueError, AttributeError):
                ts = None
            if ts is None or ts >= cutoff:
                kept.append(raw if raw.endswith("\n") else raw + "\n")
            else:
                removed += 1
        if removed:
            tmp_path = STORE + ".tmp"
            with _lock:
                os.makedirs(STORE_DIR, exist_ok=True)
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.writelines(kept)
                os.replace(tmp_path, STORE)
            logger.info(f"滚动清理 {removed} 条过期记录（保留 {retention_days} 天）")
        return removed
    except Exception as e:
        logger.error(f"打分流清理失败: {e}")
        return 0
