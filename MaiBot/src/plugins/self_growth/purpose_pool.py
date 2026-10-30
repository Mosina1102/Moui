"""目的机制·动机池（S1，M3.2，吸收自 SurfCore purpose-engine）

解决什么：主动行为原来是"掷骰时机+临场发挥"，闲逛只拿到"上网吧想干嘛干嘛"，
随机性大、无沉淀。动机池把记忆+新信息压成候选动机，掷骰命中后挑一条
"此刻能做也值得做"的再出门——素材不足就不出门：
没有自己想干的事，她只是个会回复的机器人。

数据流：
  collect_materials（主人私聊/活跃群/人物卡/心情）
    → LLM 压缩成 2-3 条候选（request_type="purpose"，走 32B 判断类槽）
    → 注入闲逛会话 user_text【你现在的目的】
    → 会话结束 settle_idle_session 记 history + 收尾语流露检测存 pending
  S3 对话收尾（maybe_note_closure → note_closure）也产 pending（带 origin_stream）。

分层决策（2审定案）：
  reason="ok"              正常注入目的块，占社交目的额度
  reason="browse_fallback" 素材不足/额度用完 → 纯冲浪降级出门（每日限1次，不占社交额度，三审R-A15）
  reason="no_material"     素材不足且 browse 额度也用完 → 闲逛取消（没目的不出门）
  reason="fault"           LLM/IO 故障 → 返回空串但闲逛照常开、照常占额（fail-open 保功能）

占额时机：闲逛正式启动时（入口处 build_purpose_block 内），不是结算时——
闲逛本身烧多轮 token，"silent 不占额"会被无限绕过（1审红坑③，对齐 SurfCore
"投递成功即占额：模型回合已花钱"）。

铁律：fail-open（任何异常只记日志，绝不阻断闲逛主链路）；同步 IO 在 async 链路
一律 asyncio.to_thread（1审红坑R2：全目录同步 IO 阻塞事件循环）；
写入口唯一（状态文件只走 _load_state/_save_state）；getattr 防御读配置；
常量化放文件头部（DSH 公式公开惯例）。
"""
import asyncio
import json
import os
import re
import time
from typing import Dict, List, Tuple

from src.common.logger_manager import get_logger

logger = get_logger("purpose_pool")

# ===== 全局参数（可调，对齐 SurfCore purpose-engine 常量） =====
PURPOSE_DAILY_MAX = 4  # 每日带目的出门次数（对齐 SurfCore dailyMax=4）
PENDING_TTL_DAYS = 14  # pending 有效期（过期作废，对齐 SurfCore）
MIN_MATERIALS = 2  # 素材不足不出门——没有真目的就不主动
HISTORY_MAX = 60  # history 上限（对齐 SurfCore 60 条，超量从最旧删）
BROWSE_DAILY_MAX = 1  # 素材不足/额度用完时"纯冲浪"降级出门每日限额（三审R-A15：无目的冲浪是拟真的一部分，只是不值得占社交目的额度）
PENDING_MAX_HITS = 3  # 同一条 pending 被带出门 3 次还没着落就放弃（对齐 SurfCore retry 15min×3 的放弃语义）
PENDING_MAX = 8  # pending 池容量上限（防堆积，超量丢最旧）
CLOSURE_COOLDOWN_MINUTES = 30  # S3 收尾冷却：刚说"先这样"的会话 30min 内 pending 冻结（变相硬续=1审红坑⑤）
CLOSURE_DAILY_PER_STREAM = 1  # S3 防刷：每会话每日最多记 1 条收尾惦记
PENDING_DIRECT_CHANCE = 0.5  # 未过期 pending 直接作为目的的概率（对齐 SurfCore pending 50% 优先）
OWNER_CACHE_SECONDS = 3600  # 主人身份推断缓存时长（config 未配 owner_qq 时从私聊流推断，别每次掷骰都聚合）
OWNER_BUSY_MINUTES = 10  # S1 接线点三：主人最近 N 分钟在私聊说过话 → 本轮跳过（主人在忙就陪着，不自己玩）

# 收尾词边界（2审收口）："留空间""怕刷屏""等他先说"不算收尾——那是潜水不是收束，收进去会让 next_opening 泛滥
CLOSURE_WORDS = ("收尾", "结束", "翻篇", "到此为止", "先这样")
# 收尾语流露检测：闲逛收摊的话里带这些字样 → 存 pending（宁缺毋滥，正则不命中就不存）
PEND_WORD_RE = re.compile(r"下次|改天|回头|再看看|再查查|再去|还想")

STATE_FILE = os.path.join("data", "self_memory", "purpose_pool.json")
_PERSON_CARD_DIR = os.path.join("data", "person_cards")

# 纯冲浪降级注入块（三审R-A15）
_BROWSE_BLOCK = (
    "【你现在的目的】\n"
    "最近没什么攒着的事，就是想上网随便逛逛：刷刷空间看看大家最近在干嘛，"
    "搜点感兴趣的新鲜东西，逛到有意思的可以随手分享。别硬凑，逛不动就 DONE。"
)

KIND_LABELS = {"share": "分享", "ask": "想弄明白", "curious": "想围观", "connect": "想找人说说话"}

# ✎v2 压缩 PROMPT（手册全文，placeholder 自填）
COMPRESS_PROMPT = """你正在帮一个QQ群里的女孩整理她这几天积攒的念头。下面是她的近期素材（群聊片段/私聊/记忆）。
只从这些真实素材出发，提炼 2-3 条她"此刻想做也值得做"的事。要求：
- 每条必须能追溯到具体素材，禁止编造和泛泛（"想找人聊天"这种不算）
- 群友的话题只有反复出现且和她有关时才算她的——一次性的别人的话题不是她的目的
- kind 四选一：share（想分享个东西）/ask（想弄明白某事）/curious（想围观某人某事）/connect（想找某人说话）
- 每条附 next_opening：如果她决定做这件事，开场第一句可以怎么说（口语、15字内）
素材：
{materials}
心情状态：{mood}
只输出 JSON 数组，不要其他内容：[{{"kind":"...","text":"...","next_opening":"..."}}]"""

# 主人身份推断缓存（R-A13 红坑修法：Moui 没有"主人是谁"的配置时不能让最高权重素材路悬空）
_owner_cache: Dict[str, float] = {"qq": "", "at": 0.0}


# ================= 状态读写（写入口唯一） =================

def _load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.error(f"[动机池] 状态文件读失败（按空状态继续）: {e}")
    return {}


def _save_state(state: dict) -> None:
    """低频写盘（掷骰命中进会话时 / 结算时 / 收尾时）。先写 .tmp 再 os.replace 原子替换。"""
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        logger.error(f"[动机池] 状态文件写失败: {e}")


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _day_reset(state: dict) -> bool:
    """跨天重置（占额/browse 额度/收尾防刷计数）。重置时机=首次跨天调用（对齐 SurfCore"推到次日起点"）。"""
    today = _today()
    if state.get("day") != today:
        state["day"] = today
        state["count_today"] = 0
        state["count_browse_today"] = 0
        state["closure_day"] = {}
        return True
    return False


def _pending_valid(p: dict, now: float, state: dict = None) -> bool:
    """pending 有效性（纯函数，测试直抄）：TTL 未过期 + hits 未达上限 + 收尾冷却外。"""
    if not isinstance(p, dict):
        return False
    if float(p.get("expires_at", 0) or 0) < now:
        return False
    if int(p.get("hits", 0) or 0) >= PENDING_MAX_HITS:
        return False
    # S3 收尾冷却：刚说"先这样"的会话，30min 内它的惦记冻结（被@/被提问的正常回复不受影响）
    sid = str(p.get("origin_stream", "") or "")
    if sid and state is not None:
        closure_ts = float((state.get("closure_at") or {}).get(sid, 0) or 0)
        if closure_ts and now - closure_ts < CLOSURE_COOLDOWN_MINUTES * 60:
            return False
    return True


# ================= 主人身份（R-A13） =================

def get_owner_qq() -> str:
    """主人QQ：config owner_qq 优先；空则从私聊流推断（近7天最近活跃的私聊好友，
    聚合范本照抄 proactive_manager._get_friend_uins）并缓存。"""
    try:
        cfg_qq = ""
        try:
            from src.config.config import global_config

            cfg_qq = str(getattr(global_config, "BOT_OWNER_QQ", "") or "").strip()
        except Exception:
            pass
        if cfg_qq:
            return cfg_qq
        now = time.time()
        if _owner_cache["qq"] and now - _owner_cache["at"] < OWNER_CACHE_SECONDS:
            return _owner_cache["qq"]
        inferred = ""
        try:
            from src.common.database import db

            week_ago = now - 7 * 86400
            cursor = db.messages.aggregate(
                [
                    {"$match": {"chat_info.group_info": None, "time": {"$gte": week_ago}}},
                    {"$group": {"_id": "$chat_info.user_info.user_id", "last": {"$max": "$time"}}},
                    {"$sort": {"last": -1}},
                    {"$limit": 1},
                ]
            )
            for d in cursor:
                if d.get("_id"):
                    inferred = str(d["_id"])
        except Exception as e:
            logger.debug(f"[动机池] 主人身份推断失败: {e}")
        _owner_cache["qq"] = inferred
        _owner_cache["at"] = now
        return inferred
    except Exception as e:
        logger.error(f"[动机池] get_owner_qq 异常: {e}")
        return ""


def _owner_talked_recently(owner: str, minutes: int) -> bool:
    """主人最近 N 分钟在私聊说过话（同步，调用方 to_thread）。user_id 字段 str/int 双匹配（照抄 _query_last_message_times 防呆）。"""
    try:
        from src.common.database import db

        since = time.time() - minutes * 60
        doc = db.messages.find_one(
            {
                "chat_info.group_info": None,
                "user_info.user_id": {"$in": [owner, int(owner) if str(owner).isdigit() else owner]},
                "time": {"$gte": since},
            }
        )
        return bool(doc)
    except Exception as e:
        logger.debug(f"[动机池] 主人活跃查询失败: {e}")
        return False


async def is_owner_recently_active() -> bool:
    """S1 接线点三：主人最近在私聊说话 → True（主人在忙就陪着，不自己玩）。
    fail-open：查询异常返回 False（不阻塞掷骰）。"""
    try:
        owner = await asyncio.to_thread(get_owner_qq)
        if not owner:
            return False
        return await asyncio.to_thread(_owner_talked_recently, owner, OWNER_BUSY_MINUTES)
    except Exception as e:
        logger.debug(f"[动机池] 主人在忙检测异常: {e}")
        return False


# ================= 素材收集 =================

def _get_active_group_ids(limit: int = 3) -> List[str]:
    """近 24h 活跃群（按消息数）。聚合范本照抄 proactive_manager._get_friend_uins。"""
    try:
        from src.common.database import db

        day_ago = time.time() - 86400
        cursor = db.messages.aggregate(
            [
                {"$match": {"chat_info.group_info": {"$ne": None}, "time": {"$gte": day_ago}}},
                {"$group": {"_id": "$chat_id", "n": {"$sum": 1}}},
                {"$sort": {"n": -1}},
                {"$limit": limit},
            ]
        )
        return [str(d["_id"]) for d in cursor if d.get("_id")]
    except Exception as e:
        logger.debug(f"[动机池] 活跃群查询失败: {e}")
        return []


def _recent_card_events(limit: int = 5) -> List[str]:
    """活跃前 N 张人物卡的最新事件（按文件 mtime 排序取前 N 张，禁止全量读——1审红坑R2）。"""
    out: List[str] = []
    try:
        if not os.path.isdir(_PERSON_CARD_DIR):
            return out
        files = []
        for fname in os.listdir(_PERSON_CARD_DIR):
            if fname.endswith(".json"):
                try:
                    files.append((os.path.getmtime(os.path.join(_PERSON_CARD_DIR, fname)), fname))
                except OSError:
                    continue
        files.sort(reverse=True)
        for _mt, fname in files[:limit]:
            try:
                with open(os.path.join(_PERSON_CARD_DIR, fname), "r", encoding="utf-8") as f:
                    card = json.load(f)
                events = card.get("events") or []
                if events:
                    out.append(str(events[-1]))
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"[动机池] 记忆卡事件读取失败: {e}")
    return out


async def collect_materials() -> Tuple[List[str], str]:
    """三路素材收集（同步 IO 全部 to_thread）。返回 (素材行列表, 心情文本)。
    心情不进素材列表，进 LLM 压缩的倾向判断（手册设计）。"""
    lines: List[str] = []
    now = time.time()

    # a) 主人私聊 30 条（最高权重素材路）
    owner = await asyncio.to_thread(get_owner_qq)
    if owner:
        try:
            from ..utils.chat_message_builder import get_raw_msg_before_timestamp_with_users

            msgs = await asyncio.to_thread(
                get_raw_msg_before_timestamp_with_users, timestamp=now, person_ids=[owner], limit=30
            )
            text = " ".join(str(m.get("processed_plain_text", "") or "") for m in (msgs or []))
            if text.strip():
                lines.append(f"[主人私聊] {text[:600]}")
        except Exception as e:
            logger.debug(f"[动机池] 主人私聊素材跳过: {e}")

    # b) 活跃群前 3 各 5 条
    try:
        group_ids = await asyncio.to_thread(_get_active_group_ids, 3)
        if group_ids:
            from ..utils.chat_message_builder import get_raw_msg_before_timestamp_with_chat

            for gid in group_ids:
                try:
                    msgs = await asyncio.to_thread(
                        get_raw_msg_before_timestamp_with_chat, chat_id=gid, timestamp=now, limit=5
                    )
                    text = " ".join(str(m.get("processed_plain_text", "") or "") for m in (msgs or []))
                    if text.strip():
                        lines.append(f"[群{gid}] {text[:400]}")
                except Exception as e:
                    logger.debug(f"[动机池] 群 {gid} 素材跳过: {e}")
    except Exception as e:
        logger.debug(f"[动机池] 群素材跳过: {e}")

    # c) 记忆卡最新事件（最多 5 条）
    try:
        for ev in await asyncio.to_thread(_recent_card_events, 5):
            if ev:
                lines.append(f"[记忆] {ev[:120]}")
    except Exception as e:
        logger.debug(f"[动机池] 记忆卡素材跳过: {e}")

    mood_text = ""
    try:
        from ..moods.moods import MoodManager

        mood = MoodManager.get_instance().get_current_mood()
        mood_text = f"{mood.text}(valence={mood.valence:.2f}, arousal={mood.arousal:.2f})"
    except Exception:
        pass
    return lines, mood_text


# ================= LLM 压缩 =================

async def _compress_purpose(material_lines: List[str], mood_text: str) -> List[dict]:
    """LLM 压缩素材 → 候选动机。解析失败 raise（由调用方归为 fault）。"""
    from src.config.config import global_config as cfg
    from ..models.utils_model import LLMRequest

    prompt = COMPRESS_PROMPT.format(materials="\n".join(material_lines)[:8000], mood=mood_text or "平静")
    llm = LLMRequest(model=cfg.llm_summary, request_type="purpose", temperature=0.7, max_tokens=500)
    # generate_response 返回 (content, reasoning, model_name[, tool_calls])——防御式取首元素，不依赖元数
    resp = await llm.generate_response(prompt)
    response = resp[0] if isinstance(resp, tuple) and resp else resp
    m = re.search(r"\[.*\]", str(response or ""), re.DOTALL)
    if not m:
        raise ValueError(f"动机压缩无 JSON 数组: {str(response)[:80]}")
    items = json.loads(m.group(0))
    out: List[dict] = []
    for it in items if isinstance(items, list) else []:
        try:
            kind = str(it.get("kind", "")).strip()
            text = str(it.get("text", "")).strip()[:80]
            if kind in KIND_LABELS and text:
                out.append(
                    {
                        "kind": kind,
                        "text": text,
                        "next_opening": str(it.get("next_opening", "") or "").strip()[:30],
                    }
                )
        except Exception:
            continue
    return out[:3]


# ================= 注入块构建 =================

def _build_purpose_block(candidates: List[dict], pendings: List[dict], state: dict) -> str:
    """构建【你现在的目的】注入块（纯函数，验收观测点：user_text 含该标题）。"""
    lines = ["【你现在的目的】", "你不是随便上来逛的，心里攒着这几件事（优先做，做完或做不成再自由逛）："]
    for i, c in enumerate(candidates, 1):
        line = f"{i}. ({KIND_LABELS.get(c.get('kind', ''), c.get('kind', ''))}) {c.get('text', '')}"
        opening = c.get("next_opening", "")
        if opening:
            line += f"——开场可以像：「{opening}」"
        lines.append(line)
    now = time.time()
    fresh = [p for p in pendings if _pending_valid(p, now, state)]
    if fresh:
        lines.append("还有惦记没了的：")
        for p in fresh[:2]:
            lines.append(f"· 上次这段收在「{str(p.get('summary', ''))[:60]}」，可以的话接着聊聊或问问后续")
    lines.append("要分享/搭话用 send_chat_message，target 从可分享对象里选；别硬凑，做不了就坦白逛不动。")
    return "\n".join(lines)


def _touch_pending_hits(state: dict, now: float) -> None:
    """被注入的 pending hits+1（被带出门一次；≥PENDING_MAX_HITS 后 _pending_valid 自动淘汰）。"""
    for p in state.get("pending", []) or []:
        if _pending_valid(p, now, state):
            p["hits"] = int(p.get("hits", 0) or 0) + 1


# ================= 主入口：build_purpose_block =================

async def build_purpose_block() -> Tuple[str, str]:
    """掷骰命中进闲逛前调用（proactive_manager._idle_browse_session 内，P4 世界卡块之前）。
    返回 (注入块, reason)，reason ∈ ("ok"|"browse_fallback"|"no_material"|"fault")：
    - ok / browse_fallback / fault → 闲逛照常开（区别在注入块与占额）
    - no_material → 闲逛取消（_idle_browse_session 据此 return，本轮主动不出门）
    """
    state = await asyncio.to_thread(_load_state)
    try:
        _day_reset(state)
        now = time.time()

        social_quota_left = state.get("count_today", 0) < PURPOSE_DAILY_MAX

        # 素材收集（只在还有社交额度时做，省 IO）
        materials: List[str] = []
        mood_text = ""
        if social_quota_left:
            try:
                materials, mood_text = await collect_materials()
            except Exception as e:
                logger.error(f"[动机池] 素材收集异常: {e}")
                materials, mood_text = [], ""

        # 分层决策：额度用完 / 素材不足 → browse 降级（R-A15）或取消
        if not social_quota_left:
            logger.info("[动机池] 今日目的额度用完")
        elif len(materials) < MIN_MATERIALS:
            logger.info("[动机池] 素材不足")
        if not social_quota_left or len(materials) < MIN_MATERIALS:
            if state.get("count_browse_today", 0) < BROWSE_DAILY_MAX:
                state["count_browse_today"] = state.get("count_browse_today", 0) + 1
                await asyncio.to_thread(_save_state, state)
                logger.info("[动机池] browse 降级出门（无目的冲浪，不占社交目的额度）")
                return _BROWSE_BLOCK, "browse_fallback"
            logger.info("[动机池] 素材不足，本轮主动取消")
            await asyncio.to_thread(_save_state, state)
            return "", "no_material"

        # LLM 压缩
        try:
            candidates = await _compress_purpose(materials, mood_text)
        except Exception as e:
            # 系统故障：闲逛照常开、照常占额（fail-open 保功能，1审红坑①）
            logger.error(f"[动机池] 压缩故障降级，无目的闲逛: {e}")
            state["count_today"] = state.get("count_today", 0) + 1
            await asyncio.to_thread(_save_state, state)
            return "", "fault"

        if not candidates:
            # LLM 正常但提炼不出能追溯的动机 = 没有真目的 → 同素材不足处理
            logger.info("[动机池] 无可追溯目的")
            if state.get("count_browse_today", 0) < BROWSE_DAILY_MAX:
                state["count_browse_today"] = state.get("count_browse_today", 0) + 1
                await asyncio.to_thread(_save_state, state)
                logger.info("[动机池] browse 降级出门（无目的冲浪，不占社交目的额度）")
                return _BROWSE_BLOCK, "browse_fallback"
            logger.info("[动机池] 无目的，本轮主动取消")
            await asyncio.to_thread(_save_state, state)
            return "", "no_material"

        # 占额（闲逛正式启动）+ pending hits 回写 + 落盘
        state["count_today"] = state.get("count_today", 0) + 1
        _touch_pending_hits(state, now)
        await asyncio.to_thread(_save_state, state)

        block = _build_purpose_block(candidates, state.get("pending", []) or [], state)
        logger.info(
            f"[动机池] 已注入 {len(candidates)} 条候选（今日 {state['count_today']}/{PURPOSE_DAILY_MAX}）: {block[:60]}"
        )
        return block, "ok"
    except Exception as e:
        # 最外层兜底：状态异常时不占额（连状态都读不到，占额无从谈起），闲逛照常开
        logger.error(f"[动机池] build_purpose_block 异常（降级无目的闲逛）: {e}")
        return "", "fault"


# ================= 会话结算 =================

async def settle_idle_session(session_state: dict = None, last_content: str = "") -> None:
    """闲逛会话结束结算（_idle_browse_session finally 调用）：
    - 按 session_state 记 history（outcome=sent/silent，只影响 history 不影响占额）
    - 收尾语流露"下次再看看X"→ 存 pending（宁缺毋滥，正则不命中不存）"""
    try:
        state = await asyncio.to_thread(_load_state)
        _day_reset(state)
        sent = int((session_state or {}).get("sent_count", 0) or 0) > 0
        text = str(last_content or "").strip()
        summary = text[:80] if text else ("分享了内容" if sent else "没说话，潜水收摊")
        state.setdefault("history", []).append(
            {"at": time.time(), "kind": "idle", "summary": summary, "outcome": "sent" if sent else "silent"}
        )
        state["history"] = state["history"][-HISTORY_MAX:]
        if text and len(text) <= 120 and PEND_WORD_RE.search(text):
            pending_list = state.setdefault("pending", [])
            pending_list.append(
                {
                    "summary": text[:80],
                    "next_opening": "",
                    "created_at": time.time(),
                    "expires_at": time.time() + PENDING_TTL_DAYS * 86400,
                    "hits": 0,
                    "origin_stream": "",  # 闲逛流露无明确归属：群聊注入时标注为"上网时惦记的"
                }
            )
            # pending 池上限：超量丢最旧
            while len(pending_list) > PENDING_MAX:
                pending_list.pop(0)
            logger.info(f"[动机池] 从收尾语捕获惦记: {text[:40]}")
        await asyncio.to_thread(_save_state, state)
    except Exception as e:
        logger.error(f"[动机池] 结算失败: {e}")


# ================= S3：对话收尾 → pending =================

async def note_closure(stream_id: str, summary: str) -> None:
    """记录收尾惦记（S3）。summary 必须是"对话收在哪"（最近消息正文），
    不是拒发理由——2审黄坑Y3：收在「话题已翻篇」是语义错位。"""
    try:
        sid = str(stream_id or "")
        if not sid:
            return
        state = await asyncio.to_thread(_load_state)
        _day_reset(state)
        # 防刷：每会话每日最多 1 条
        if (state.setdefault("closure_day", {}) or {}).get(sid) == _today():
            return
        state["closure_day"][sid] = _today()
        state.setdefault("closure_at", {})[sid] = time.time()
        pending_list = state.setdefault("pending", [])
        pending_list.append(
            {
                "summary": str(summary or "")[:80],
                "next_opening": "",  # 由 S1 的 LLM 压缩步骤顺带生成（手册设计；当前简化为回灌只展示"收在「…」"）
                "created_at": time.time(),
                "expires_at": time.time() + PENDING_TTL_DAYS * 86400,
                "hits": 0,
                "origin_stream": sid,  # A 群的惦记不许串到 B 群（2审Y2）
            }
        )
        while len(pending_list) > PENDING_MAX:
            pending_list.pop(0)
        await asyncio.to_thread(_save_state, state)
        try:
            from .self_memory import write_journal

            write_journal("purpose_closure", {"stream_id": sid, "summary": str(summary or "")[:50]})
        except Exception:
            pass
        logger.info(f"[动机池] 记录收尾惦记({sid}): {str(summary or '')[:40]}")
    except Exception as e:
        logger.error(f"[动机池] note_closure 异常: {e}")


async def maybe_note_closure(stream_id: str, reason_text: str) -> None:
    """S3 接线入口（heartFC_chat 两处一行调用）：拒发理由/no_reply 理由含收尾词 → 记收尾惦记。
    summary 不取 reason（拒发理由≠对话摘要），取该会话最近 1-2 条消息正文（to_thread）。
    fail-open：任何异常只记 debug，绝不影响回复主链路。"""
    try:
        text = str(reason_text or "")
        if not stream_id or not any(w in text for w in CLOSURE_WORDS):
            return
        summary = ""
        try:
            from ..utils.chat_message_builder import get_raw_msg_before_timestamp_with_chat

            msgs = await asyncio.to_thread(
                get_raw_msg_before_timestamp_with_chat, chat_id=str(stream_id), timestamp=time.time(), limit=2
            )
            summary = " ".join(
                str(m.get("processed_plain_text", "") or "") for m in (msgs or [])
            ).strip()[:80]
        except Exception as e:
            logger.debug(f"[动机池] 取收尾上下文失败: {e}")
        if not summary:
            return
        await note_closure(stream_id, summary)
    except Exception as e:
        logger.debug(f"[动机池] maybe_note_closure 跳过: {e}")
