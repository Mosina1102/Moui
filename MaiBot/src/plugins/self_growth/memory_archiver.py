"""方案L：记忆归档器（海马体Max 归档层核心）。

每日凌晨对活跃群执行一次归纳：当日对话流 + 海马体图记忆 → LLM 一次调用同时产出
人物卡增量更新（person_updates）与情节卡（episodic），应用进 person_cards / episodic_cards。

铁律：全程 try/except fail-open（归档失败只记日志，绝不打断主聊天链路）；
LLM 调用与解析按方案写死，禁止施工时自行删改 prompt。
"""
import time
from typing import List

from src.common.logger_manager import get_logger
from src.config.config import global_config
from src.plugins.models.utils_model import LLMRequest
from src.plugins.PFC.pfc_utils import get_items_from_json

logger = get_logger("memory_archiver")

# 素材防呆：对话文本上限（500条原始消息折叠后可能超长，控制归档调用成本）
_DIALOG_CAP = 12000
# 海马体检索素材窗口
_HIPPO_TEXT_CHARS = 2000

ARCHIVE_SYSTEM = """你是记忆归档员。每天凌晨你把昨天的群聊整理成两类记忆卡，替"她"记住真正值得记住的东西。

你的产出（只输出 JSON，不输出任何其他文字，不要 markdown 围栏）：
{"person_updates": [...], "episodic": [...]}

【person_updates 每条字段】
{"qq": "对方QQ号(纯数字字符串)", "name": "名字", "impression_update": "本轮对她新了解到的一点(增量句，30字内，如'游戏群里爱接梗')", "interaction_update": "相处方式的增量补充(增量句，25字内，没有就空串)", "pet_peeves": ["雷点，最多3个，没有就空数组"], "events": [{"date": "YYYY-MM-DD", "text": "今日值得记住的事件(30字内)", "evidence": "群里真实原话片段"}], "evidence": "本次更新的依据(群里真实原话片段)"}

【person_updates 铁律】
- impression_update/interaction_update 是**增量句**：只写本轮新了解到的一点，禁止复述或改写旧印象（系统会自动拼接，写全量句反而污染）
- 只写有依据的更新：找不到依据的人直接跳过，不许凭空建卡
- evidence 必须从【群聊记录】里逐字摘取，禁止编造；禁止引用Moui（BOT_NICKNAME）自己的发言当证据（"拍了拍/戳了戳"系统动作消息除外，且此类证据只许用于 events）
- events 是今日新增的事件（0-2条），不重复旧档案里已有的事件

【episodic 每条字段】
{"date": "YYYY-MM-DD", "persons": ["涉及的人的QQ号"], "text": "发生了什么+她的感受(60字内，用'我'的口吻)", "evidence": "群里真实原话片段", "importance": 1到5的整数}

【episodic 记忆点五判据——满足其一才算，全不满足就不输出这条】
①梗的诞生（今天新造的梗、被反复接的梗）
②情绪起伏（有人大笑/破防/生气/感动）
③关系里程碑（第一次互动、和解、重大帮忙）
④具体锚物（对话围绕某个具体的物/图/事——"烤鱼表情包"是记忆点，"聊了天"不是）
⑤她自己被打动的瞬间
- text 必须落在具体细节上（带锚物或原话片段）；写成话题概述（"在群里聊了画画"）= 废卡，直接不输出
- 普通寒暄、日常接龙不配入卡；每天宁缺毋滥
- importance 锚点：5=关系里程碑（第一次互动/和解/重大帮忙），4=梗的诞生（后续还被接），3=情绪起伏的具体瞬间，2=有锚物的日常，1=勉强够格；**拿不准就压低一档**
- date 照抄 user_text 里给的"本批素材日期"（素材主体是昨天，禁止自填今天）
- episodic 只收【海马体相关记忆】里没有的具体新事件；海马体已覆盖的泛化内容不要重复建卡（免得同源记忆想起两遍）

【输出格式】
一个 JSON 对象，两个字段都是数组，没有内容就是空数组。不要解释。"""


def _format_hippo_text(memories: list) -> str:
    """海马体检索结果 [(topic, memory_items, similarity), ...] → 文本段（每条截200字）。"""
    lines: List[str] = []
    for item in memories or []:
        try:
            topic, memory_items, _sim = item
            parts = [str(memory_items)] if not isinstance(memory_items, list) else [str(x) for x in memory_items]
            line = f"· [{topic}] {'；'.join(parts)}"[:200]
            if line.strip():
                lines.append(line)
        except Exception:
            continue
    return "\n".join(lines)


def _format_cards_text(stream_id: str, top_users: list) -> str:
    """在场用户 top10 的现有卡摘要（impression/interaction 各50字）。"""
    from .person_cards import get_card

    lines: List[str] = []
    for uid in top_users[:10]:
        try:
            card = get_card(str(uid))
            if not card:
                continue
            name = str(card.get("name", "") or uid)
            impression = str(card.get("impression", "") or "")[:50]
            interaction = str(card.get("interaction", "") or "")[:50]
            seg = f"· {name}：{impression}" if impression else f"· {name}："
            if interaction:
                seg += f"；相处：{interaction}"
            lines.append(seg)
        except Exception:
            continue
    return "\n".join(lines)


async def archive_group(stream_id: str) -> bool:
    """对单群执行一次归纳。返回 True=完成（含死群/素材不足等正常跳过），False=可重试失败（LLM 429/网络/解析）。

    失败信号供调度器做 60s→300s 退避重试。
    """
    try:
        if not stream_id:
            return True
        from ..chat.chat_stream import chat_manager
        from src.plugins.utils.chat_message_builder import (
            build_readable_messages,
            get_raw_msg_before_timestamp_with_chat,
        )
        from .person_cards import apply_person_updates
        from .episodic_cards import append_cards

        chat_stream = chat_manager.get_stream(stream_id)
        if chat_stream is None:
            return True

        now = time.time()
        min_msgs = int(getattr(global_config, "min_msgs_to_archive", 20) or 20)

        # 1a. 当日对话（limit 500 + ts 后过滤对齐 24h 窗口，同 group_reviewer 款式）
        msgs = get_raw_msg_before_timestamp_with_chat(chat_id=stream_id, timestamp=now, limit=500)
        msgs = [m for m in msgs if float(m.get("time", 0) or 0) >= now - 86400]
        if len(msgs) < min_msgs:
            return True

        # 1b. 可读对话
        chat_log = await build_readable_messages(
            msgs, replace_bot_name=True, merge_messages=True, timestamp_mode="normal", read_mark=0.0
        )
        if not chat_log or len(chat_log.strip()) < 100:
            return True
        chat_log = chat_log[:_DIALOG_CAP]

        # 1c. 海马体原料（坑⑪实证：检索路径只读无副作用；fast_retrieval=True 省一次 llm_topic_judge）
        hippo_text = ""
        try:
            from src.plugins.memory_system.Hippocampus import HippocampusManager

            memories = await HippocampusManager.get_instance().get_memory_from_text(
                chat_log[:_HIPPO_TEXT_CHARS],
                max_memory_num=10,
                max_memory_length=3,
                max_depth=3,
                fast_retrieval=True,
            )
            hippo_text = _format_hippo_text(memories)
        except Exception as e:
            logger.warning(f"[K·归档] 海马体原料拉取失败（跳过该段）: {e}")

        # 1d. 现有卡摘要：在场用户（当日发言者）按发言次数 top10
        user_counts: dict = {}
        for m in msgs:
            ui = m.get("user_info") or {}
            uid = str(ui.get("user_id") or "")
            if uid:
                user_counts[uid] = user_counts.get(uid, 0) + 1
        top_users = [uid for uid, _c in sorted(user_counts.items(), key=lambda kv: -kv[1])[:10]]
        cards_text = _format_cards_text(stream_id, top_users)

        # 1e. 素材日期（坑⑧）：中位时间戳所在日期——"过去24h"素材主体是昨天
        times_sorted = sorted(float(m.get("time", 0) or 0) for m in msgs)
        mid_ts = times_sorted[len(times_sorted) // 2] if times_sorted else now
        material_date = time.strftime("%Y-%m-%d", time.localtime(mid_ts))

        bot_name = str(getattr(global_config, "BOT_NICKNAME", "") or "Moui")

        # 2. LLM 归纳（写死）
        user_text = (
            f"现在是 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}，bot名字是{bot_name}。\n"
            f"本批素材日期={material_date}（所有卡片的 date 照抄此值，禁止自填）。\n\n"
            f"【群聊记录】\n{chat_log}\n\n"
            + (f"【海马体相关记忆】\n{hippo_text}\n\n" if hippo_text else "")
            + (f"【现有档案（在此基础缓变）】\n{cards_text}\n\n" if cards_text else "")
            + "请按系统要求归档，只输出 JSON。"
        )
        llm = LLMRequest(
            model=global_config.llm_normal,
            request_type="memory_archive",
            temperature=0.3,
            max_tokens=2000,
        )
        response, _, _ = await llm.generate_response(user_text, system_prompt=ARCHIVE_SYSTEM)
        content = response or ""

        # 3. 解析
        ok, data = get_items_from_json(
            content,
            "记忆归档",
            "person_updates",
            "episodic",
            default_values={"person_updates": [], "episodic": []},
            allow_empty=True,
        )
        if not ok or not isinstance(data, dict):
            logger.warning(f"[K·归档] 归纳输出解析失败，原文前200字: {content[:200]}")
            return False  # 多半是429/网络抖动，供调度器退避重试

        person_updates = data.get("person_updates")
        episodic = data.get("episodic")
        if not isinstance(person_updates, list):  # 参数防呆：非 list 视为空
            person_updates = []
        if not isinstance(episodic, list):
            episodic = []
        # episodic 卡补 chat_id（归档器知道 stream_id，LLM 不知道）
        for card in episodic:
            if isinstance(card, dict) and not card.get("chat_id"):
                card["chat_id"] = stream_id

        # 4. 应用
        n = apply_person_updates(person_updates, chat_log, bot_name)
        m = append_cards(episodic, chat_log, bot_name)

        # 5. 日志
        group_name = ""
        try:
            group_name = chat_manager.get_stream_name(stream_id) or ""
        except Exception:
            pass
        logger.info(f"[K·归档] {group_name or stream_id[:8]}: 人物卡更新{n}条, 情节卡{m}条")
        return True
    except Exception as e:
        logger.error(f"[K·归档] 单群归纳异常（跳过）: {e}")
        return False  # 多半是429/网络抖动，供调度器退避重试


async def pre_forget_safety_archive() -> None:
    """[方案L二期C] 遗忘前保险：原版遗忘按比例删图节点前，把将被淡忘的、涉及活跃人物卡人物的
    记忆尽力捞进事件时间线。

    【坑K诚实化】保险是"尽力捞"，不是"精确拦截"——按名字检索到的记忆与遗忘实际删除的低激活
    节点不对齐，本函数不保证救回所有将被删除的记忆。定位：遗忘前的低成本补救（限 3 人/次）。
    全程 try/except fail-open（main.py 调用处还有一层兜底）。"""
    try:
        if not getattr(global_config, "person_cards_enable", False):
            return
        from .person_cards import load_all_cards, get_card, save_card

        now = time.time()
        # updated_at 距今 >7 天的卡（近期归档过的无需再捞）
        stale = [
            card
            for card in load_all_cards().values()
            if now - float(card.get("updated_at", 0) or 0) > 7 * 86400
        ]
        if not stale:
            return
        stale.sort(key=lambda c: float(c.get("updated_at", 0) or 0))  # 最久没更新的优先
        saved_count = 0
        from src.plugins.memory_system.Hippocampus import HippocampusManager

        for card in stale[:3]:  # 限 3 人/次（防 token 爆炸）
            try:
                qq = str(card.get("qq", "") or "")
                name = str(card.get("name", "") or "")
                if not qq or not name:
                    continue
                impression = str(card.get("impression", "") or "")[:40]
                memories = await HippocampusManager.get_instance().get_memory_from_text(
                    f"{name} {impression}",
                    max_memory_num=5,
                    max_memory_length=2,
                    max_depth=2,
                    fast_retrieval=True,
                )
                if not memories:
                    continue
                events = card.get("events") or []
                covered_text = "".join(str(e.get("text", "") or "") for e in events if isinstance(e, dict))
                new_events = []
                for item in memories:
                    try:
                        topic, memory_items, _sim = item
                        parts = [str(memory_items)] if not isinstance(memory_items, list) else [str(x) for x in memory_items]
                        fragment = f"[{topic}] {'；'.join(parts)}"[:80]
                    except Exception:
                        continue
                    # 覆盖判定：片段前10字已 in 卡内 events 拼接文本 → 跳过
                    probe = fragment[:10]
                    if not probe or probe in covered_text:
                        continue
                    new_events.append(
                        {
                            "date": time.strftime("%Y-%m-%d", time.localtime(now - 7 * 86400)),
                            "text": fragment,
                            "evidence": f"海马体图记忆: {fragment[:30]}",
                        }
                    )
                if not new_events:
                    continue
                card["events"] = (events + new_events)[: int(getattr(global_config, "card_max_events", 8) or 8)]
                save_card(card)
                saved_count += len(new_events)
            except Exception as e:
                logger.error(f"[K·遗忘保险] 单人补救失败: {e}")
                continue
        if saved_count:
            logger.info(f"[K·遗忘保险] 遗忘前补救 {saved_count} 条事件入卡")
    except Exception as e:
        logger.error(f"[K·遗忘保险] 归档保险异常（跳过）: {e}")
