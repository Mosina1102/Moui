"""方案L：情节卡（海马体Max 归档层）——可检索的共同回忆。

存储：data/episodic_cards/episodic.jsonl，一行一卡。
统一大脑每轮调用 get_episodic_block 注入"共同回忆"（动态区）。

实现纪律（挖坑报告坑④，K坑④复发防线）：
- 进程内缓存 _cache + _lock（照抄 score_events 锁纪律：锁内 O(1)，解析锁外）
- 启动后首次调用一次性加载 jsonl 进缓存，此后读操作全走缓存——统一大脑每轮调用零磁盘 IO
- 命中注入的卡：锁内只改缓存中的 last_recalled 并登记脏 id；落盘由调度器每日 cleanup
  统一执行（脏行随全量重写落盘）——统一大脑思考路径上禁止发生任何文件重写
- append_cards / cleanup 经缓存操作 + 统一刷盘（凌晨归档路径，非思考路径）

像人三件套：①激活强化（age_days 按 last_recalled 计，被想起过的记忆衰减更慢）
②记忆模糊化（>60天的卡注入加"模糊"前缀）③闪光灯记忆（importance=5 不衰减）。
"""
import json
import os
import threading
import time
from typing import Dict, List, Optional, Set

from src.common.logger_manager import get_logger
from src.config.config import global_config
from .self_memory import write_journal
from .person_cards import _evidence_check

logger = get_logger("episodic_cards")

STORE_DIR = os.path.join("data", "episodic_cards")
STORE = os.path.join(STORE_DIR, "episodic.jsonl")

# 软容量上限（方案未定数值，防无限膨胀的自卫线；闪光灯卡只在"其余卡全清后仍超容量"时让位）
_MAX_CARDS = 500

_cache: Dict[str, dict] = {}  # {id -> card}
_dirty_ids: Set[str] = set()  # last_recalled 已变待落盘的卡
_loaded = False
_lock = threading.Lock()


def _cfg(key: str, default):
    """getattr 防部署漂移（铁律3）。"""
    try:
        return getattr(global_config, key, default)
    except Exception:
        return default


def _load_if_needed() -> None:
    """启动后首次调用一次性加载 jsonl 进缓存（锁内只读原始行，解析锁外）。"""
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
                logger.error(f"情节卡读取失败: {e}")
                raw_lines = []
        # 解析在锁外
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
        logger.error(f"情节卡缓存初始化失败: {e}")
        with _lock:
            _loaded = True  # 防反复重试刷屏


def _flush() -> None:
    """全量重写 jsonl（原子替换）。仅归档/清理路径调用（凌晨调度，非思考路径）。"""
    try:
        with _lock:
            snapshot = list(_cache.values())
            _dirty_ids.clear()
        lines = [json.dumps(c, ensure_ascii=False) for c in snapshot if isinstance(c, dict)]
        os.makedirs(STORE_DIR, exist_ok=True)
        tmp = STORE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
        os.replace(tmp, STORE)
    except Exception as e:
        logger.error(f"情节卡落盘失败: {e}")


def _weight(card: dict, now_ts: float) -> float:
    """排序权重 = importance × 0.5^(age_days / half_life_days)。
    激活强化：age_days 按 last_recalled 计（无此字段用 created_at）；
    闪光灯（importance=5）：不参与衰减（age_days 视为 0）。"""
    try:
        importance = float(card.get("importance", 1) or 1)
    except (TypeError, ValueError):
        importance = 1.0
    if importance >= 5:
        return importance
    anchor = float(card.get("last_recalled", 0) or 0) or float(card.get("created_at", 0) or 0)
    age_days = max(0.0, (now_ts - anchor) / 86400) if anchor else 999.0
    try:
        half_life = float(_cfg("card_half_life_days", 45) or 45)
    except (TypeError, ValueError):
        half_life = 45.0
    half_life = max(1.0, half_life)
    return importance * (0.5 ** (age_days / half_life))


def _next_id(now_ts: float) -> str:
    return f"e{int(now_ts * 1000)}{int(now_ts * 1e6) % 100:02d}"


def append_cards(cards: list, dialog_text: str, bot_nickname: str) -> int:
    """归档器调用。逐条：evidence 校验 + 反自证校验（规则同 person_cards.apply_person_updates）
    → 通过才追加。单群单日超 episodic_max_per_day 截断（按 importance 降序保留）。
    返回成功条数。"""
    _load_if_needed()
    added: List[dict] = []
    now_ts = time.time()
    try:
        if not isinstance(cards, list):
            return 0
        bot_nickname = str(bot_nickname or "")
        for card in cards:
            try:
                if not isinstance(card, dict):
                    continue
                text = str(card.get("text", "") or "").strip()
                if not text:
                    continue
                ok, reason = _evidence_check(card, dialog_text, bot_nickname)
                if not ok:
                    logger.warning(f"情节卡被拒（{reason}）: {text[:40]}")
                    continue
                try:
                    importance = max(1, min(5, int(card.get("importance", 2) or 2)))
                except (TypeError, ValueError):
                    importance = 2
                persons = card.get("persons")
                added.append(
                    {
                        "id": _next_id(now_ts + len(added)),
                        "date": str(card.get("date", "") or "")[:10],
                        "chat_id": str(card.get("chat_id", "") or ""),
                        "persons": [str(p) for p in persons] if isinstance(persons, list) else [],
                        "text": text[:100],
                        "evidence": str(card.get("evidence", "") or "").strip()[:80],
                        "importance": importance,
                        "created_at": now_ts,
                        "last_recalled": None,
                    }
                )
            except Exception as e:
                logger.error(f"情节卡单条处理失败: {e}")
                continue

        if not added:
            return 0

        # 单群单日截断：已有卡 + 新卡合并按 importance 降序保留（同分保留已有），超限淘汰
        with _lock:
            try:
                max_per_day = int(_cfg("episodic_max_per_day", 3) or 3)
            except (TypeError, ValueError):
                max_per_day = 3
            groups: Dict[tuple, List[dict]] = {}
            for c in _cache.values():
                key = (str(c.get("chat_id", "") or ""), str(c.get("date", "") or ""))
                groups.setdefault(key, []).append(c)
            for c in added:
                groups.setdefault((c["chat_id"], c["date"]), []).append(c)
            final_ids = set()
            for bucket in groups.values():
                if len(bucket) > max_per_day:
                    bucket.sort(
                        key=lambda c: (-float(c.get("importance", 1) or 1), float(c.get("created_at", 0) or 0))
                    )
                for c in bucket[:max_per_day]:
                    final_ids.add(str(c["id"]))
            # 已有卡：不在保留名单的删（顺带执行容量纪律）
            for c in list(_cache.values()):
                if str(c["id"]) not in final_ids:
                    del _cache[c["id"]]
            # 新卡：在保留名单的入缓存
            applied = 0
            for c in added:
                if str(c["id"]) in final_ids:
                    _cache[str(c["id"])] = c
                    applied += 1
        if applied:
            _flush()  # 凌晨归档路径，直接刷盘
            write_journal("episodic_append", {"count": applied})
        return applied
    except Exception as e:
        logger.error(f"情节卡追加失败: {e}")
        return 0


def _mood_valence() -> float:
    """当前愉悦度（方案L二期E：心情染色）。MoodManager 异常/未初始化时回 0（无染色）。"""
    try:
        from ..moods.moods import MoodManager

        return float(MoodManager.get_instance().get_valence())
    except Exception:
        return 0.0


_POSITIVE_WORDS = ("开心", "好玩", "笑", "喜欢", "快乐", "感动")  # 方案12积极词
_NEGATIVE_WORDS = ("难过", "哭", "生气", "破防", "emo", "累")  # 消极词表（方案未列全，补常用低落词）


def _mood_match(text: str, valence: float) -> int:
    """mood_match：卡 text 含积极词且愉悦度>0.2 → 1；含消极词且愉悦度<-0.2 → 1；其余 0。"""
    t = str(text or "")
    if valence > 0.2 and any(w in t for w in _POSITIVE_WORDS):
        return 1
    if valence < -0.2 and any(w in t for w in _NEGATIVE_WORDS):
        return 1
    return 0


def get_episodic_block(person_ids: set, chat_id: str, now_ts: float) -> str:
    """统一大脑每轮调用（思考路径：只读缓存 + 锁内 O(1) 改 last_recalled，零磁盘 IO）。

    筛选：persons 与 person_ids 有交集 或 chat_id 相同；排序按 _weight；
    取前 episodic_inject_num 条，每条限100字；>60天的卡加"（这段记忆有些模糊了）"前缀。
    返回空串（无可注入）或：【共同回忆】\\n· (10-27) 和搞搞聊烤鱼梗…挺开心
    注意：动态区内容（带日期会变），不进稳定区。"""
    try:
        _load_if_needed()
        chat_id = str(chat_id or "")
        person_ids = {str(p) for p in (person_ids or set())}
        with _lock:
            snapshot = list(_cache.values())  # 锁内 O(n) 快照复制，筛选/打分/排序全在锁外
        candidates: List[dict] = []
        for c in snapshot:
            if str(c.get("chat_id", "") or "") == chat_id:
                candidates.append(dict(c))
                continue
            if person_ids:
                persons = {str(p) for p in (c.get("persons") or [])}
                if person_ids & persons:
                    candidates.append(dict(c))
        if not candidates:
            return ""
        _mv = _mood_valence()  # 心情染色：愉悦度循环外取一次（每轮思考只读一次 MoodManager）
        for c in candidates:
            # 方案L二期E：心情染色——权重 ×(1 + mood_match×0.3)，只影响注入排序不改衰减/删除
            c["_w"] = _weight(c, now_ts) * (1 + _mood_match(c.get("text", ""), _mv) * 0.3)
        candidates.sort(key=lambda c: -c["_w"])
        try:
            inject_num = int(_cfg("episodic_inject_num", 2) or 2)
        except (TypeError, ValueError):
            inject_num = 2
        picked = candidates[:inject_num]

        hit_ids: List[str] = []
        lines: List[str] = []
        for c in picked:
            created = float(c.get("created_at", 0) or 0)
            date_raw = str(c.get("date", "") or "")
            date_disp = date_raw[5:10] if len(date_raw) >= 10 else date_raw  # MM-DD
            text = str(c.get("text", "") or "").strip()[:100]
            if not text:
                continue
            if created and (now_ts - created) > 60 * 86400:
                text = f"（这段记忆有些模糊了）{text}"
            lines.append(f"· ({date_disp}) {text}")
            hit_ids.append(str(c["id"]))

        if not lines:
            return ""

        # 激活强化登记：锁内只改缓存 + 登记脏 id，落盘由每日 cleanup 统一执行
        if hit_ids:
            with _lock:
                for cid in hit_ids:
                    card = _cache.get(cid)
                    if card is not None:
                        card["last_recalled"] = now_ts
                        _dirty_ids.add(cid)

        return "\n【共同回忆】\n" + "\n".join(lines) + "\n"
    except Exception as e:
        logger.error(f"构建情节卡注入块失败: {e}")
        return ""


def cleanup_expired(now_ts: float) -> int:
    """权重 < 0.05 的卡删除（importance=5 闪光灯卡豁免衰减删除，仅在其余卡全清后仍超容量时
    按最旧删除）。重写 jsonl 原子替换。调度器每日一次调用。返回删除数。
    顺带把 _dirty_ids 的 last_recalled 变更落盘。写 journal（op="episodic_cleanup"）。"""
    try:
        _load_if_needed()
        with _lock:
            all_cards = list(_cache.values())
        survivors: List[dict] = []
        removed = 0
        for c in all_cards:
            if float(c.get("importance", 1) or 1) >= 5:
                survivors.append(c)  # 闪光灯先无条件保留
                continue
            if _weight(c, now_ts) < 0.05:
                removed += 1
            else:
                survivors.append(c)
        # 超软容量：闪光灯卡按最旧让位（仅在其余卡全清后仍超容量的兜底）
        if len(survivors) > _MAX_CARDS:
            survivors.sort(key=lambda c: float(c.get("created_at", 0) or 0))
            excess = len(survivors) - _MAX_CARDS
            survivors = survivors[excess:]
            removed += excess
        with _lock:
            _cache.clear()
            for c in survivors:
                _cache[str(c["id"])] = c
        _flush()
        if removed:
            write_journal("episodic_cleanup", {"removed": removed, "remaining": len(survivors)})
            logger.info(f"[K·情节卡] 清理 {removed} 条，剩余 {len(survivors)} 条")
        return removed
    except Exception as e:
        logger.error(f"情节卡清理失败: {e}")
        return 0
