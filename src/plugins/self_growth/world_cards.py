"""方案L二期B：世界卡（工具知识沉淀）。

她通过 web_search/web_fetch 学到的事实性知识沉淀为可注入的世界卡——下次遇到相关话题
"我知道这事"不靠重新搜索；通用搜索照旧走 web_search 工具，世界卡只是"已经查过的"快速回忆。

实现纪律（同 episodic_cards 坑④防线）：进程内缓存 + 锁，读出端（统一大脑思考路径）零磁盘 IO；
写入端（工具路径）与清理端（调度路径）经缓存操作 + 统一刷盘。
匹配规则（坑J）：jieba 分词取≥2字词项交集，否定词/代词禁匹配——禁止裸"≥2字公共子串"。
"""
import json
import os
import threading
import time
from typing import Dict, List

from src.common.logger_manager import get_logger
from src.config.config import global_config

logger = get_logger("world_cards")

STORE_DIR = os.path.join("data", "world_cards")
STORE = os.path.join(STORE_DIR, "world_cards.jsonl")

_MAX_CARDS = 200
_TOPIC_DUP_CHARS = 10  # 查重：topic 前10字重复视为同话题
_MATCH_MIN_LEN = 2  # 匹配词项最小长度
_BLOCK_CHARS = 60  # 注入每条限60字
# 停用豁免集：否定词/代词禁匹配（坑J）
_STOP_WORDS = {"没有", "不是", "可以", "我们", "你们", "自己", "什么", "这个", "那个"}

_cache: Dict[str, dict] = {}  # {id -> card}
_loaded = False
_lock = threading.Lock()


def _cfg(key: str, default):
    try:
        return getattr(global_config, key, default)
    except Exception:
        return default


def _load_if_needed() -> None:
    global _loaded
    if _loaded:
        return
    try:
        with _lock:
            if _loaded:
                return
            try:
                with open(STORE, "r", encoding="utf-8") as f:
                    raw_lines = f.readlines()
            except FileNotFoundError:
                raw_lines = []
            except Exception as e:
                logger.error(f"世界卡读取失败: {e}")
                raw_lines = []
        parsed: Dict[str, dict] = {}
        for raw in raw_lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
                if isinstance(item, dict) and item.get("id"):
                    parsed[str(item["id"])] = item
            except json.JSONDecodeError:
                continue
        with _lock:
            _cache.update(parsed)
            _loaded = True
    except Exception as e:
        logger.error(f"世界卡缓存初始化失败: {e}")
        with _lock:
            _loaded = True


def _flush() -> None:
    """全量重写 jsonl（原子替换）。仅工具/清理路径调用，思考路径禁触发。"""
    try:
        with _lock:
            snapshot = list(_cache.values())
        lines = [json.dumps(c, ensure_ascii=False) for c in snapshot if isinstance(c, dict)]
        os.makedirs(STORE_DIR, exist_ok=True)
        tmp = STORE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
        os.replace(tmp, STORE)
    except Exception as e:
        logger.error(f"世界卡落盘失败: {e}")


def _cut_words(text: str) -> set:
    """jieba 分词取 ≥2 字词项集合（排除停用词）。jieba 已是现成依赖（海马体 fast_retrieval 同款）。"""
    try:
        import jieba

        return {
            w
            for w in jieba.lcut(str(text or ""))
            if len(w) >= _MATCH_MIN_LEN and w not in _STOP_WORDS
        }
    except Exception:
        return set()


def append_world_card(topic: str, text: str, source: str) -> bool:
    """查重（topic 前10字 in 已有 topic → 覆盖更新而非追加）；上限 200 条（超了按权重删最弱）。
    写 journal（op="world_card"）。工具路径调用，全吞异常由调用方保证。"""
    try:
        _load_if_needed()
        topic = str(topic or "").strip()[:60]
        text = str(text or "").strip()[:80]
        if not topic or not text:
            return False
        now_ts = time.time()
        with _lock:
            # 查重：topic 前10字互相命中 → 覆盖更新（双向包含）
            dup_id = ""
            for cid, card in _cache.items():
                existing_topic = str(card.get("topic", "") or "")
                e_head = existing_topic[:_TOPIC_DUP_CHARS]
                t_head = topic[:_TOPIC_DUP_CHARS]
                if (e_head and e_head in topic) or (t_head and t_head in existing_topic):
                    dup_id = cid
                    break
            if dup_id:
                card = _cache[dup_id]
                card["text"] = text
                card["source"] = str(source or "")[:20]
                card["created_at"] = now_ts  # 覆盖视为重新学到，重置衰减锚点
            else:
                card = {
                    "id": f"w{int(now_ts * 1000)}",
                    "topic": topic,
                    "text": text,
                    "source": str(source or "")[:20],
                    "created_at": now_ts,
                    "half_life_days": 30,
                    "last_recalled": None,
                }
                _cache[card["id"]] = card

            # 容量：超 200 条按权重删最弱（权重 = 1 × 0.5^(age/30)）
            if len(_cache) > _MAX_CARDS:
                scored = sorted(
                    _cache.items(),
                    key=lambda kv: 0.5 ** (max(0.0, now_ts - float(kv[1].get("created_at", 0) or 0)) / 86400 / 30),
                )
                for cid, _c in scored[: len(_cache) - _MAX_CARDS]:
                    del _cache[cid]
        _flush()
        try:
            from .self_memory import write_journal

            write_journal("world_card", {"topic": topic[:30], "source": str(source or ""), "updated": bool(dup_id)})
        except Exception as e:
            logger.error(f"世界卡 journal 写入失败: {e}")
        return True
    except Exception as e:
        logger.error(f"世界卡写入失败: {e}")
        return False


def get_world_cards_block(current_topic_text: str) -> str:
    """统一大脑检测到搜索意图/话题命中时调用（思考路径：只读缓存零磁盘 IO）。
    返回空串或：【我知道的事】\\n· XX画展：10月在上海举办…"""
    try:
        _load_if_needed()
        topic_words = _cut_words(str(current_topic_text or "")[:200])
        if not topic_words:
            return ""
        now_ts = time.time()
        with _lock:
            snapshot = list(_cache.values())
        hits: List[tuple] = []
        for c in snapshot:
            card_words = _cut_words(str(c.get("topic", "")) + str(c.get("text", "")))
            if topic_words & card_words:
                age_days = max(0.0, (now_ts - float(c.get("created_at", 0) or 0)) / 86400)
                w = 0.5 ** (age_days / 30.0)  # 半衰期 30 天衰减排序
                hits.append((w, c))
        if not hits:
            return ""
        hits.sort(key=lambda t: -t[0])
        lines: List[str] = []
        for _w, c in hits[:2]:
            text = str(c.get("text", "") or "").strip()[:_BLOCK_CHARS]
            topic = str(c.get("topic", "") or "").strip()[:30]
            if text:
                lines.append(f"· {topic}：{text}")
        if not lines:
            return ""
        return "\n【我知道的事】\n" + "\n".join(lines) + "\n"
    except Exception as e:
        logger.error(f"构建世界卡注入块失败: {e}")
        return ""


def cleanup_world_cards(now_ts: float) -> int:
    """半衰期 30 天衰减，权重 <0.05 删除。调度器每日调用。返回删除数。"""
    try:
        _load_if_needed()
        with _lock:
            snapshot = list(_cache.values())
        removed = 0
        survivors: List[dict] = []
        for c in snapshot:
            age_days = max(0.0, (now_ts - float(c.get("created_at", 0) or 0)) / 86400)
            if 0.5 ** (age_days / 30.0) < 0.05:
                removed += 1
            else:
                survivors.append(c)
        if removed:
            with _lock:
                _cache.clear()
                for c in survivors:
                    _cache[str(c["id"])] = c
            _flush()
            try:
                from .self_memory import write_journal

                write_journal("world_card_cleanup", {"removed": removed, "remaining": len(survivors)})
            except Exception:
                pass
            logger.info(f"[K·世界卡] 清理 {removed} 条，剩余 {len(survivors)} 条")
        return removed
    except Exception as e:
        logger.error(f"世界卡清理失败: {e}")
        return 0
