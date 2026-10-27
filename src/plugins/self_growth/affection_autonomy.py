"""方案L二期F：好感自主（她可自主申报好感调整）。

设计立场（老大拍板）：**不限速**——"一下子下头"本身就是人性，禁止用工程洁癖阉割。
只保留三样工程安全网：数值 clamp（防溢出报错）、journal 流水（可回滚审计）、
表达层走五级映射平滑文本（她改的是数值，注入的永远是缓变态度文本）。

链路：统一大脑输出 affection_update 字段 → heartFC 调 apply_affection_update →
名字反查 qq → person_info.add_relationship_delta 增量落库 → 内存趋势索引 + 人物卡联动 + journal。
"""
import json
import os
import time
from collections import deque
from typing import Dict, List, Optional

from src.common.logger_manager import get_logger
from src.config.config import global_config

logger = get_logger("affection_autonomy")

JOURNAL_FILE = os.path.join("data", "self_memory", "journal.jsonl")

# 内存趋势索引（坑R）：{qq -> deque(maxlen=3)}，13.3 关系动向注入只读此索引（零磁盘 IO）
_trend_index: Dict[str, deque] = {}
_backfilled = False


def _ensure_backfill() -> None:
    """开机回填：模块首次被调用时惰性读 journal 尾部 50 行，过滤 op="affection_autonomy"
    按 qq 重建 _trend_index（journal 审计副本兼冷备份）——重启后关系动向叙述不丢。"""
    global _backfilled
    if _backfilled:
        return
    _backfilled = True
    try:
        if not os.path.exists(JOURNAL_FILE):
            return
        with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()[-50:]
        rebuilt: Dict[str, deque] = {}
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
                if str(item.get("op", "") or "") != "affection_autonomy":
                    continue
                # journal 落盘是平铺格式（self_memory._journal：{"op":..., **detail}），字段在顶层
                qq = str(item.get("qq", "") or "")
                if not qq:
                    continue
                rebuilt.setdefault(qq, deque(maxlen=3)).append(
                    {
                        "delta": int(item.get("delta", 0) or 0),
                        "reason": str(item.get("reason", "") or "")[:30],
                        "ts": float(item.get("ts", 0) or 0),
                    }
                )
            except Exception:
                continue
        # 回填不覆盖运行时已有的（运行时优先）
        for qq, dq in rebuilt.items():
            if qq not in _trend_index:
                _trend_index[qq] = dq
        if rebuilt:
            logger.info(f"[好感自主] journal 回填 {len(rebuilt)} 人的关系动向")
    except Exception as e:
        logger.error(f"[好感自主] 回填失败: {e}")


def get_trend(qq: str) -> List[dict]:
    """读某人的近3条好感流水（注入端用，零磁盘 IO）。"""
    _ensure_backfill()
    try:
        dq = _trend_index.get(str(qq))
        return list(dq) if dq else []
    except Exception:
        return []


async def _resolve_qq_by_name(person_name: str, chat_id: str) -> Optional[str]:
    """按 name 反查 qq：先群成员速查（当前群精确），再人物卡兜底。"""
    person_name = str(person_name or "").strip()
    if not person_name:
        return None
    # 1) 群成员速查
    try:
        from src.plugins.chat.outbound_enhance import get_group_members
        from src.plugins.chat.chat_stream import chat_manager

        stream = chat_manager.get_stream(chat_id) if chat_id else None
        if stream is not None:
            members = await get_group_members(stream)
            for m in members or []:
                name = str(m.get("name", "") or "")
                if name == person_name:
                    return str(m.get("user_id", "") or "")
            # 模糊兜底：互相包含（LLM 给的名字可能是昵称的一部分）
            for m in members or []:
                name = str(m.get("name", "") or "")
                if name and (name in person_name or person_name in name):
                    return str(m.get("user_id", "") or "")
    except Exception as e:
        logger.warning(f"[好感自主] 速查反查失败: {e}")
    # 2) 人物卡兜底
    try:
        from .person_cards import load_all_cards

        for qq, card in load_all_cards().items():
            name = str(card.get("name", "") or "")
            if name == person_name:
                return qq
    except Exception as e:
        logger.warning(f"[好感自主] 人物卡反查失败: {e}")
    return None


async def apply_affection_update(person_name: str, delta: int, reason: str, chat_id: str) -> bool:
    """解析端转调。规则：
    1. delta clamp 到 [-5, +5]（工程防溢出，非拟真限制）；reason 为空 → 拒绝
    2. 定位 person：按 name 在群成员速查/人物卡里反查 qq
    3. 落库：person_info_manager.add_relationship_delta(qq, delta)（增量语义，clamp ±1000）
    4. 内存索引 _trend_index[qq]（近3条流水）
    5. 人物卡联动：events 追加好感条目（走查重与容量规则）
    6. journal（op="affection_autonomy"）——可回滚可审计
    7. 全程 try/except fail-open"""
    try:
        _ensure_backfill()
        person_name = str(person_name or "").strip()
        reason = str(reason or "").strip()
        if not person_name:
            return False
        if not reason:
            logger.warning("[好感自主] 申报被拒：reason 为空")
            return False
        try:
            delta = max(-5, min(5, int(float(delta))))
        except (TypeError, ValueError):
            return False
        if delta == 0:
            return False
        if not bool(getattr(global_config, "affection_autonomy_enable", True)):
            return False

        qq = await _resolve_qq_by_name(person_name, chat_id)
        if not qq:
            logger.warning(f"[好感自主] 申报被拒：找不到人 {person_name}")
            return False

        # 落库
        from src.plugins.person_info.person_info import person_info_manager

        new_value = await person_info_manager.add_relationship_delta(qq, delta)
        if new_value is None:
            return False

        # 内存趋势索引
        dq = _trend_index.setdefault(qq, deque(maxlen=3))
        dq.append({"delta": delta, "reason": reason[:30], "ts": time.time()})

        # 人物卡联动（异步不阻塞主流程：apply 里是本地文件写，直接做）
        try:
            from .person_cards import get_card, save_card

            card = get_card(qq)
            if card is not None:
                events = card.get("events") or []
                entry = {
                    "date": time.strftime("%Y-%m-%d"),
                    "text": f"好感{'+' if delta > 0 else ''}{delta}（{reason[:20]}）",
                    "evidence": reason[:80],
                }
                dup = any(
                    str(e.get("text", "") or "")[:10] == entry["text"][:10] for e in events if isinstance(e, dict)
                )
                if not dup:
                    events.append(entry)
                    card["events"] = events
                    save_card(card)
        except Exception as e:
            logger.error(f"[好感自主] 人物卡联动失败: {e}")

        # journal（审计副本）
        try:
            from .self_memory import write_journal

            write_journal(
                "affection_autonomy",
                {"person": person_name, "qq": qq, "delta": delta, "reason": reason, "chat_id": str(chat_id or ""), "ts": time.time()},
            )
        except Exception as e:
            logger.error(f"[好感自主] journal 写入失败: {e}")

        logger.info(f"[好感自主] {person_name}({qq}) 好感{delta:+d}：{reason[:40]}（现值 {new_value:.0f}）")
        return True
    except Exception as e:
        logger.error(f"[好感自主] 申报处理失败: {e}")
        return False
