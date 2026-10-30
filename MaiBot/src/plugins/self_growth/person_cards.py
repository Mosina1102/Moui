"""方案L：人物卡（海马体Max 归档层）。

每好友一张档案卡（data/person_cards/{qq}.json）：印象/相处方式/雷点/事件时间线/兴趣。
凌晨由 memory_archiver 归纳产出增量更新（apply_person_updates），聊天时在场人物卡
经 get_person_cards_block 定向注入统一大脑稳定区（"我认识他"不靠检索运气）。

铁律：fail-open（任何异常只记日志）；审计（每次写卡 journal）；缓变拼接（增量句拼接
不覆盖，超长按"；"整句删最旧，禁止按字符切腰斩半句）；反自证（她的发言不能当对人的
评价证据，"拍了拍/戳了戳"系统动作消息豁免但仅限支撑 events）。
"""
import json
import os
import time
from typing import Dict, List, Optional, Tuple

from src.common.logger_manager import get_logger
from .self_memory import write_journal

logger = get_logger("person_cards")

STORE_DIR = os.path.join("data", "person_cards")

# 缓变拼接上限（方案3.2第3条：总长超100字按"；"从最旧的整句删除）
_MERGE_MAX_CHARS = 100
# 单条增量句入端截断（prompt 限30字，这里防御性放宽）
_INC_MAX_CHARS = 60
# 注入块每人限120字
_PERSON_BLOCK_CHARS = 120


def _ensure_dir() -> None:
    os.makedirs(STORE_DIR, exist_ok=True)


def _card_path(qq: str) -> str:
    return os.path.join(STORE_DIR, f"{qq}.json")


def get_card(qq: str) -> Optional[dict]:
    """读单张卡；文件不存在返回 None。读失败返回 None 并记日志。"""
    try:
        path = _card_path(str(qq))
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            card = json.load(f)
        if isinstance(card, dict):
            return card
        return None
    except Exception as e:
        logger.error(f"读人物卡失败 qq={qq}: {e}")
        return None


def save_card(card: dict) -> bool:
    """写卡（先写 .tmp 再 os.replace 原子替换）。card_max_events 裁剪 events 保留最新 N 条。
    写 journal（op="person_card_update"）。"""
    try:
        qq = str(card.get("qq", "") or "")
        if not qq:
            return False
        _ensure_dir()

        # events 容量裁剪：新的顶旧的
        try:
            max_events = int(getattr(global_config_lazy(), "card_max_events", 8) or 8)
        except Exception:
            max_events = 8
        events = card.get("events")
        if isinstance(events, list) and len(events) > max_events:
            card["events"] = events[-max_events:]

        card["qq"] = qq
        card["updated_at"] = time.time()

        path = _card_path(qq)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(card, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

        try:
            write_journal(
                "person_card_update",
                {
                    "qq": qq,
                    "name": str(card.get("name", "") or ""),
                    "impression": str(card.get("impression", "") or "")[:60],
                    "events_count": len(card.get("events") or []),
                },
            )
        except Exception as e:
            logger.error(f"人物卡 journal 写入失败: {e}")
        return True
    except Exception as e:
        logger.error(f"写人物卡失败 qq={card.get('qq')}: {e}")
        return False


def global_config_lazy():
    """延迟取 global_config（独立小函数避免顶层 import 环，同 unified_brain.global_config_normal_temp 款式）。"""
    from src.config.config import global_config

    return global_config


def _merge_incremental(old: str, inc: str) -> str:
    """缓变拼接（坑③⑤核心修法，禁止整体覆盖）：增量句与旧值拼接（"旧印象；"+新句），
    总长超100字时按"；"分隔符从最旧的整句删除（坑L：禁止按字符切，会腰斩半句）。
    下一轮归纳的【现有档案】会让 LLM 在摘要时自然压缩。"""
    old = str(old or "").strip()
    inc = str(inc or "").strip()[:_INC_MAX_CHARS]
    if not inc:
        return old
    if not old:
        return inc
    merged = f"{old}；{inc}"
    while len(merged) > _MERGE_MAX_CHARS and "；" in merged:
        parts = merged.split("；")
        parts.pop(0)  # 删最旧的整句
        merged = "；".join(parts)
    return merged


def _find_evidence_hit_line(dialog_text: str, evidence_key: str) -> str:
    """定位 dialog_text 中包含 evidence_key 的行（行格式 `名字:内容`）。找不到返回空串。"""
    for line in dialog_text.splitlines():
        if evidence_key in line:
            return line
    return ""


def _evidence_check(update: dict, dialog_text: str, bot_nickname: str) -> Tuple[bool, str]:
    """证据校验（坑③⑤⑨修订版）。返回 (是否通过, 拒绝原因)。

    1. evidence 前12字必须在 dialog_text 中 in 匹配，否则整条拒绝（编造嫌疑）
    2. 反自证：命中行以 bot_nickname 开头 → 拒绝；豁免：命中行含"拍了拍/戳了戳"（系统动作
       消息）时放行，但该 evidence 只允许支撑 events——带它且含 impression_update/
       interaction_update 的更新整条拒绝
    """
    evidence = str(update.get("evidence", "") or "").strip()
    if not evidence:
        return False, "证据为空"
    evidence_key = evidence[:12]

    # 豁免判定先行：系统动作消息的关键词可能不在前12字内，先按关键词在全文找命中行
    hit_line = _find_evidence_hit_line(dialog_text, evidence_key)
    if not hit_line:
        hit_line = _find_evidence_hit_line(dialog_text, evidence.strip())
    if not hit_line:
        return False, "证据编造嫌疑（原话片段未在群聊记录中出现）"

    is_system_action = ("拍了拍" in hit_line) or ("戳了戳" in hit_line)
    if hit_line.strip().startswith(bot_nickname):
        if not is_system_action:
            return False, "反自证（她自己的发言不能当对人的评价证据）"
        # 系统动作豁免：仅允许支撑 events
        if str(update.get("impression_update", "") or "").strip() or str(
            update.get("interaction_update", "") or ""
        ).strip():
            return False, "系统动作证据仅允许支撑事件，不得支撑印象/相处方式"
    return True, ""


def apply_person_updates(updates: list, dialog_text: str, bot_nickname: str) -> int:
    """归档器调用。逐条处理，返回成功应用条数。

    规则：evidence 校验 → 反自证校验（含系统动作豁免）→ 缓变拼接（增量句，禁止整体覆盖）
    → 字段清洗（pet_peeves 去重合并最多6条 / events 追加去重超量从最旧删）。
    """
    applied = 0
    if not isinstance(updates, list):
        return 0
    bot_nickname = str(bot_nickname or "")

    for update in updates:
        try:
            if not isinstance(update, dict):
                continue
            qq = str(update.get("qq", "") or "").strip()
            if not qq.isdigit():
                continue  # qq 字段非纯数字字符串 → 跳过

            ok, reason = _evidence_check(update, dialog_text, bot_nickname)
            if not ok:
                logger.warning(f"人物卡更新被拒（{reason}）: qq={qq} evidence={str(update.get('evidence', ''))[:40]}")
                continue

            card = get_card(qq)
            if card is None:
                card = {
                    "qq": qq,
                    "name": str(update.get("name", "") or qq),
                    "impression": "",
                    "interaction": "",
                    "pet_peeves": [],
                    "events": [],
                    "interests": [],
                }
            if update.get("name"):
                card["name"] = str(update["name"])[:30]

            changed = False

            # 缓变拼接：增量句写入，禁止整体覆盖
            impression_update = str(update.get("impression_update", "") or "").strip()
            if impression_update:
                card["impression"] = _merge_incremental(card.get("impression", ""), impression_update)
                changed = True
            interaction_update = str(update.get("interaction_update", "") or "").strip()
            if interaction_update:
                card["interaction"] = _merge_incremental(card.get("interaction", ""), interaction_update)
                changed = True

            # pet_peeves：旧+新合并去重，最多6条
            new_peeves = update.get("pet_peeves")
            if isinstance(new_peeves, list) and new_peeves:
                old_peeves = [str(p) for p in (card.get("pet_peeves") or []) if str(p).strip()]
                merged = []
                for p in old_peeves + [str(p).strip()[:20] for p in new_peeves if str(p).strip()]:
                    if p and p not in merged:
                        merged.append(p)
                card["pet_peeves"] = merged[:6]
                if merged[:6] != old_peeves:
                    changed = True

            # events：追加去重（按 date+text 前缀查重），容量在 save_card 内裁剪
            new_events = update.get("events")
            if isinstance(new_events, list) and new_events:
                old_events = card.get("events") or []
                seen_keys = {
                    (str(e.get("date", "") or ""), str(e.get("text", "") or "")[:10]) for e in old_events if isinstance(e, dict)
                }
                for ev in new_events:
                    if not isinstance(ev, dict):
                        continue
                    ev_date = str(ev.get("date", "") or "")[:10]
                    ev_text = str(ev.get("text", "") or "").strip()[:60]
                    ev_evidence = str(ev.get("evidence", "") or "").strip()[:80]
                    if not ev_text:
                        continue
                    key = (ev_date, ev_text[:10])
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    old_events.append({"date": ev_date, "text": ev_text, "evidence": ev_evidence})
                    changed = True
                card["events"] = old_events

            if not changed:
                continue
            if save_card(card):
                applied += 1
        except Exception as e:
            logger.error(f"人物卡更新单条应用失败: {e}")
            continue
    return applied


def get_person_cards_block(person_items: list) -> str:
    """注入块构建。person_items: [(qq, name), ...]（在场活跃人物，调用方截前5）。

    返回空串（无人有卡时）或：
    【你对群成员的了解】
    - 某某：温柔细腻爱画画；相处：顺着聊别抬杠；雷点：被催、阴阳怪气
    每人限120字（截断），整块超 inject_chars_limit 时从最后一人开始丢弃。
    内容分钟级稳定（吃 KV）。"""
    try:
        if not person_items:
            return ""
        try:
            chars_limit = int(getattr(global_config_lazy(), "inject_chars_limit", 600) or 600)
        except Exception:
            chars_limit = 600

        lines: List[str] = []
        for qq, name in person_items:
            card = get_card(str(qq))
            if not card:
                continue
            parts: List[str] = []
            impression = str(card.get("impression", "") or "").strip()
            interaction = str(card.get("interaction", "") or "").strip()
            peeves = [str(p) for p in (card.get("pet_peeves") or []) if str(p).strip()]
            if impression:
                parts.append(impression)
            if interaction:
                parts.append(f"相处：{interaction}")
            if peeves:
                parts.append(f"雷点：{'、'.join(peeves)}")
            if not parts:
                continue
            line = f"- {name}：{'；'.join(parts)}"
            if len(line) > _PERSON_BLOCK_CHARS:
                line = line[:_PERSON_BLOCK_CHARS]
            lines.append(line)

        if not lines:
            return ""
        # 整块超限从最后一人开始丢弃
        header = "【你对群成员的了解】\n"
        while lines and len(header) + sum(len(l) + 1 for l in lines) > chars_limit:
            lines.pop()
        if not lines:
            return ""
        return "\n" + header + "\n".join(lines) + "\n"
    except Exception as e:
        logger.error(f"构建人物卡注入块失败: {e}")
        return ""


def get_relation_trend_block(qq: str) -> str:
    """[方案L二期F] 读 affection_autonomy 的内存索引 `_trend_index[qq]`（坑R：禁止读 journal
    文件——每轮思考零磁盘 IO），逆序（最新在前）排列：
    【你和他的关系动向】
    · (10-26) 好感+2：因为烤鱼梗接得漂亮
    无记录返回空串。每条限30字。这是动态区内容。"""
    try:
        from .affection_autonomy import get_trend  # 函数内延迟导入（affection_autonomy 也延迟导入本模块，无环）

        trend = get_trend(str(qq))
        if not trend:
            return ""
        lines: List[str] = []
        for item in reversed(trend):  # 最新在前
            try:
                ts = float(item.get("ts", 0) or 0)
                date_disp = time.strftime("%m-%d", time.localtime(ts)) if ts else "??"
                delta = int(item.get("delta", 0) or 0)
                reason = str(item.get("reason", "") or "")[:30]
                lines.append(f"· ({date_disp}) 好感{'+' if delta > 0 else ''}{delta}：{reason}")
            except Exception:
                continue
        if not lines:
            return ""
        return "\n【你和他的关系动向】\n" + "\n".join(lines) + "\n"
    except Exception as e:
        logger.error(f"构建关系动向块失败: {e}")
        return ""


def load_all_cards() -> Dict[str, dict]:
    """读目录全部卡（dict qq->card）。文件级 try/except，坏文件跳过。"""
    cards: Dict[str, dict] = {}
    try:
        if not os.path.isdir(STORE_DIR):
            return cards
        for fname in os.listdir(STORE_DIR):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(STORE_DIR, fname), "r", encoding="utf-8") as f:
                    card = json.load(f)
                if isinstance(card, dict) and str(card.get("qq", "") or ""):
                    cards[str(card["qq"])] = card
            except Exception as e:
                logger.warning(f"人物卡文件损坏跳过 {fname}: {e}")
    except Exception as e:
        logger.error(f"读人物卡目录失败: {e}")
    return cards
