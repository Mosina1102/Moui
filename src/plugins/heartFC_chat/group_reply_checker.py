# src/plugins/heartFC_chat/group_reply_checker.py
"""群聊发送前审核器（独立模型，对齐 PFC reply_checker 形态，老大拍板"统一为一个模型审核"）：

统一大脑产出 text_reply 后、发送前过一遍：
1. 审核该不该发：时效（话题翻篇没）/ 重复（跟 bot 最近已发的撞不撞）/ 场合（插话自然吗）——不合适拦截
2. 顺手修格式：该引用没引用 → need_quote 补 [引用]；该@没@ → 从群成员速查表填 at_qq；语气轻度润色

fail-open：审核异常/解析失败 → 放行原文（不因审核器故障堵死发送链路）。
"""
from typing import Optional

from src.common.logger_manager import get_logger
from src.config.config import global_config
from src.plugins.models.utils_model import LLMRequest
from src.plugins.PFC.pfc_utils import get_items_from_json

logger = get_logger("group_reply_checker")

# 审核系统提示词（固定前缀吃 KV 缓存）
CHECK_SYSTEM = """你是群聊发送前的最后一道审核，替bot把关。给你群聊最近记录和一个待发送的回复，做两件事：

1. 审核该不该发（满足任意一条就 pass=false 拒发）：
- 时效：要接的那条话题已经被翻篇（最后几条消息已经在聊别的），而且回复里没有引用锚定 → 拒发
- 重复：这句话跟 bot 最近已经发过的内容意思差不多、换个说法又说一遍 → 拒发
- 场合：回复接的话题对方已经在收尾（如"okk""哈哈"）或群里明显转场了 → 拒发
- 空话：纯附和/无信息量的接话（"太真实了""确实"式，包括复读对方刚说的话再加"也太真实了/太XX了"式评价）→ 拒发
- 较真：回复在纠正群友的错别字/口误、一本正经分析玩笑或梗的真假对错、"真的假的？"式求证、科普式指正 → 拒发（玩笑顺着接就行，较真=出戏）

2. 判断要不要引用/@（对照标准逐条检查）：
- need_quote=true 的条件（满足任意一条）：回复在接群里某条具体的发言或图片；距要回应的那条消息已经隔了2条以上消息或几分钟；群里同时有多个话题在并行
- at_qq 的条件：回复在直接跟某个具体的人说话（叫了名字/回答他的问题/接他的话头），而且群里此刻不止一个人在说话，不加@可能让人分不清在跟谁讲。从【群成员速查】里找他的QQ号；速查表里没有就留空
- 如果这条回复中并不单向一个人（比如对A说的话，后半段是给B说的）请引用B的消息或者是@B，避免混淆
- 全群随风闲聊、谁都没在点名 → 两个都不加，硬加反而刻意

3. 给这条回复打分（score，0-10 整数）。评分流程必须"先挑错、后给分"：先逐条对照下面的扣分项检查这条回复（权重=这条规矩在她心里的重要度，违反高权重=重扣），把发现的问题记下来再给分；一个扣分项都找不到，才允许给 8 分以上。
【扣分项（对齐她的群聊生存守则权重）】
【9.9级·铁律】评价别人的话："太真实了/太XX了"式价值评判、给对方的处境或选择下判断 → score ≤4，且必须 pass=false
【9.9级·铁律】机械式逻辑赞同，而不是"可以可以/有道理/不错欸"式口语化 → score ≤5
【9.8级】@规则错误：该@没@、@错人、用了调试模式昵称格式 → score ≤5，影响理解时必须 pass=false
【9.8级】虚构不存在的经历/事实（用工具查过的除外）→ score ≤4，且必须 pass=false
【9.6级】主动发言时评价别人的话而不是接话 → score ≤5
【9.1级】接自己话时重复主语、或评价自己上一条 → score ≤5
【8.9级】露AI味：书面语、客服腔、过度热情周到、排比工整像写作文 → score ≤5
【8.6级】违反碎片化分段：单段超15字、三行以上长篇 → score ≤5
【8.3级】多余内容：前后缀、冒号引号包裹、括号注释、颜文字、JSON字段名泄漏（如出现"at_qq:"字样）→ score ≤4
【8.7级】重复自己或别人的主语、接话没带新内容 → score ≤5
【7.2级】不合时宜：老称呼名字、提很久远的话题 → score ≤6
【6.0-7.8级】比喻滥用、句式重复、不推进话题 → score ≤6
【0分级·动作真实性铁律】声称做了没做过的事（发了空间/查了资料/发了语音/换了头像）→ score 0-2，且必须 pass=false
【加分项】联想式推进话题+1；接梗巧妙带新包袱+1；口语碎片化带情绪+1
【分布要求】禁止把分数扎堆在 7-8：大多数普通回复就是 5-7 分，命中的扣分项每条都要反映到分数里；有 ≤5 扣分项的回复同时必须 pass=false 拒发。你的职责是替群里的真人挑刺，不是当老好人。

4. 语气可以轻度润色（更口语/去AI腔），意思和长度不要大变，保持群聊短句风格；@昵称与正文之间必须有一个空格，缺失就补上（如"@哈基汐你好"→"@哈基汐 你好"）

reason 字段必须写：无论通过与否、无论是否修改，都必须用一句话说明判断依据；打了低分或拒发时，必须点名违反的守则条款（格式如"违反9.9禁评价" / "违反8.6分段" / "违反动作真实性铁律"），方便复盘归类。绝不许返回空字符串。

只输出 JSON（不要 markdown 围栏，reason 放第一个字段先写判断依据）。三个范例供参考格式与判断粒度：
通过范例：{"reason": "回复接的是最新一条且没人说过这个角度，无需引用无需@", "pass": true, "score": 8, "reply": "原文不动或轻度润色", "at_qq": "", "need_quote": false}
打回范例：{"reason": "要接的话题已翻篇，群里最后几条在聊别的", "pass": false, "score": 4, "reply": "", "at_qq": "", "need_quote": false}
{"reason": "一句话判断依据（必填，先写这个）", "pass": true或false, "score": 0-10整数, "reply": "修正后的最终文本（拒发时留空）", "at_qq": "需要@的成员QQ号或空字符串", "need_quote": true或false}"""


async def check_group_reply(chat_stream, reply: str, at_qq: str = "", reasoning: str = "") -> Optional[dict]:
    """审核统一大脑的群聊回复。

    返回 {"pass", "reason", "reply", "at_qq", "need_quote"}；返回 None 表示审核器故障（调用方应放行原文）。
    """
    try:
        from ..chat.chat_stream import chat_manager
        from ..chat.outbound_enhance import get_group_members
        from ..utils.chat_message_builder import build_readable_messages, get_raw_msg_before_timestamp_with_chat
        import time as _time

        if not chat_stream or not reply or not reply.strip():
            return None

        stream_id = chat_stream.stream_id
        chat_id = stream_id

        # 最近群聊（20条，绝对时间戳精确到秒——relative 模式 20 秒内全显示"刚刚"，
        # 审核器算不出精确时差导致 need_quote/时效判定失效，绝对时间配合 user_text 里的"现在"可算差值）
        context_msgs = get_raw_msg_before_timestamp_with_chat(chat_id=chat_id, timestamp=_time.time(), limit=20)
        if not context_msgs:
            return None
        chat_log = await build_readable_messages(
            context_msgs, replace_bot_name=True, merge_messages=False, timestamp_mode="normal", read_mark=0.0
        )

        # 群成员速查（补@用，前20人防膨胀）
        members_text = ""
        try:
            members = await get_group_members(chat_stream)
            if members:
                members_text = "\n".join(f"{m['user_id']}: {m['name']}" for m in members[:20])
        except Exception as e:
            logger.error(f"审核器拉取群成员失败: {e}")

        time_now = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime())
        user_text = (
            f"现在是 {time_now}，bot名字是{global_config.BOT_NICKNAME}。\n\n"
            f"【最近群聊】\n{chat_log}\n\n"
            f"【统一大脑的决策理由】{reasoning or '（未提供）'}\n\n"
            f"【待发送的回复】\n{reply}\n"
            f"（模型自带的 at_qq 字段: {at_qq or '无'}）\n\n"
            + (f"【群成员速查（QQ号: 名字）】\n{members_text}\n\n" if members_text else "")
            + "按系统要求审核并修格式，只输出 JSON。"
        )

        # 判断类任务：GLM-5.3-Flash（省57%、独立限流池不抢主聊天），短输出
        llm = LLMRequest(
            model=global_config.llm_observation,
            request_type="group_reply_check",
            temperature=0.3,
            max_tokens=350,
        )
        response, _, _ = await llm.generate_response(user_text, system_prompt=CHECK_SYSTEM)
        content = response or ""

        # 坑（验尸发现）：get_items_from_json 按 *items 白名单提取字段——
        # reason/reply/at_qq/need_quote 必须列进来，否则永远落到默认值（审核理由恒空、修正/引用/@全失效）
        ok, data = get_items_from_json(
            content,
            "群聊发送审核",
            "pass",
            "score",
            "reason",
            "reply",
            "at_qq",
            "need_quote",
            default_values={"reason": "", "reply": "", "at_qq": "", "need_quote": False, "score": 5},
            allow_array=False,
            allow_empty=True,  # 拒发时 reply/at_qq 为空串是合法值
        )
        if not ok:
            logger.warning(f"发送审核输出解析失败，放行原文。原文前100字: {content[:100]}")
            return None

        raw_reason = str(data.get("reason", "") or "").strip()
        try:
            score_val = max(0, min(10, int(float(data.get("score", 5) or 5))))
        except (TypeError, ValueError):
            score_val = 5

        # 方案K层1：打分流落盘（fail-open，异常不影响审核返回）
        try:
            from ..self_growth.score_events import append_event

            group_name = ""
            try:
                group_info = getattr(chat_stream, "group_info", None)
                group_name = getattr(group_info, "group_name", "") or ""
            except Exception:
                pass
            append_event(
                stream_id=stream_id,
                group_name=group_name,
                passed=bool(data.get("pass", True)),
                score=score_val,
                reason=raw_reason,
                reply=reply,
            )
        except Exception as e:
            logger.error(f"打分流落盘失败: {e}")

        return {
            "pass": bool(data.get("pass", True)),
            "reason": raw_reason if raw_reason else "（审核器未给理由）",
            "score": score_val,
            "reply": str(data.get("reply", "") or "").strip(),
            "at_qq": str(data.get("at_qq", "") or "").strip(),
            "need_quote": bool(data.get("need_quote", False)),
        }
    except Exception as e:
        logger.error(f"群聊发送审核异常: {e}")
        return None
