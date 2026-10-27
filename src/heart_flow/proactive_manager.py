"""主动搭话管理器（方案B）：
- 群聊版：DSH 掷骰公式 × 兴趣值门槛，命中后 LLM 生成主动话题直接发送（独立轻链路，不碰心流状态机）
- 私聊版：DSH speak-up 原版公式，命中后往 PFC 会话 goal_list 注入"主动找TA"目标（贴 PFC 目标驱动架构，零侵入 planner）

重要：首版默认试运行模式（DRY_RUN=True）——命中只记日志不真发，老大观察日志验证公式后改成 False。
"""
import asyncio
import json
import random
import time
from typing import Dict, List, Optional, Tuple

from ..config.config import global_config
from src.common.logger_manager import get_logger

logger = get_logger("proactive")

# ===== 全局参数（可调） =====
ROLL_INTERVAL_MIN = 900  # 全局节奏随机下限：15 分钟
ROLL_INTERVAL_MAX = 2400  # 全局节奏随机上限：40 分钟（打破固定周期的机械感）
JUDGE_COOLDOWN_SECONDS = 1800  # 判定硬冷却：每个对话判定过（无论中没中）30分钟内绝不二次判定
DRY_RUN_GROUP = False  # 群聊试运行开关（2026-10-26 老大要闲逛真跑，已开）：False=群聊命中后真进闲逛会话（刷空间/点赞/评论/发说说/分享到群私聊）；空间写动作有每天3条+冷却兜底，send_chat_message 每会话限2条
DRY_RUN_PRIVATE = False  # 私聊真跑：命中后真的往 PFC 注入"主动找TA"目标（私聊对象只有墨枔，风险低）

QUIET_START = 1  # 安静时段 1-8 点不掷骰
QUIET_END = 8

# 私聊版（DSH speak-up 原版公式）
PRIVATE_BASE_LINE = 20  # 基线命中线 20%
PRIVATE_RECENT_FACTOR = 0.15  # 刚聊过（<20min）时机因子：刚说完话别追着说
PRIVATE_RECENT_MINUTES = 20
PRIVATE_LONG_FACTOR = 1.5  # 冷太久（>6h）时机因子：有点想TA了
PRIVATE_LONG_HOURS = 6
PRIVATE_MISS_BONUS_STEP = 10  # 没中加成：连着没中每次+10
PRIVATE_MISS_BONUS_MAX = 40
PRIVATE_COOLDOWN_MINUTES = 20  # 命中搭话后冷却

# 群聊版（保守：群聊主动开话题比私聊冒犯性高）
GROUP_BASE_LINE = 20  # 基线命中线 20%
GROUP_COLD_FACTOR = 1.3  # 冷场 >4h 时机因子：想找人说句话
GROUP_COLD_HOURS = 4
GROUP_SELF_TALKED_FACTOR = 0.1  # 自己 <30min 前刚说过话：别连续刷存在感
GROUP_SELF_TALKED_MINUTES = 30
GROUP_MISS_BONUS_STEP = 5  # 没中加成：+5/次
GROUP_MISS_BONUS_MAX = 25
GROUP_INTEREST_THRESHOLD = 10  # 兴趣值门槛：对这个群有兴趣才主动（值域先观察日志再调）
GROUP_COOLDOWN_HOURS = 2  # 命中后每群冷却

TEXT_MAX_LEN = 60  # 主动话题长度上限


def _in_quiet_hours() -> bool:
    hour = time.localtime().tm_hour
    return QUIET_START <= hour < QUIET_END


class ProactiveManager:
    """主动搭话管理器（单例）"""

    _instance = None

    @classmethod
    def get_instance(cls) -> "ProactiveManager":
        if cls._instance is None:
            cls._instance = ProactiveManager()
        return cls._instance

    def __init__(self):
        self.subheartflow_manager = None  # 由 background_tasks 注入
        self._group_miss_streak: Dict[str, int] = {}
        self._private_miss_streak: Dict[str, int] = {}
        self._last_hit: Dict[str, float] = {}  # (g:/p:) + stream_id -> 命中时间戳
        self._last_judge: Dict[str, float] = {}  # 判定硬冷却：判定过（无论中没中）的时间戳
        self._idle_running: bool = False  # 闲逛会话进行中标志（防多会话并发）
        self._idle_cooldown_until: float = 0.0  # 全局闲逛冷却：会话结束后1小时内不再开新会话

    def setup(self, subheartflow_manager) -> None:
        self.subheartflow_manager = subheartflow_manager

    def _cooldown_ok(self, key: str, cooldown_seconds: float) -> bool:
        last = self._last_hit.get(key, 0)
        return (time.time() - last) >= cooldown_seconds

    def _judge_ok(self, key: str) -> bool:
        """判定硬冷却：该对话判定过（无论中没中）后 JUDGE_COOLDOWN_SECONDS 内不再判定"""
        last = self._last_judge.get(key, 0)
        return (time.time() - last) >= JUDGE_COOLDOWN_SECONDS

    def _query_last_message_times(self, stream_id: str) -> Tuple[float, float, float]:
        """查该聊天最近一条消息时间、Moui自己最近一条消息时间、对方最近一条消息时间"""
        from ..common.database import db

        last_any_ts, last_self_ts, last_other_ts = 0.0, 0.0, 0.0
        try:
            doc = db.messages.find_one({"chat_id": stream_id}, sort=[("time", -1)])
            if doc:
                last_any_ts = float(doc.get("time") or 0)
            bot_qq = str(global_config.BOT_QQ)
            doc = db.messages.find_one(
                {"chat_id": stream_id, "user_info.user_id": {"$in": [bot_qq, global_config.BOT_QQ]}},
                sort=[("time", -1)],
            )
            if doc:
                last_self_ts = float(doc.get("time") or 0)
            doc = db.messages.find_one(
                {"chat_id": stream_id, "user_info.user_id": {"$nin": [bot_qq, global_config.BOT_QQ]}},
                sort=[("time", -1)],
            )
            if doc:
                last_other_ts = float(doc.get("time") or 0)
        except Exception as e:
            logger.error(f"查询最近消息时间失败({stream_id}): {e}")
        return last_any_ts, last_self_ts, last_other_ts

    # ================= 群聊版 =================
    async def _roll_group(self, subheartflow) -> None:
        stream_id = subheartflow.subheartflow_id
        key = f"g:{stream_id}"

        # 群聊冷却（2h）在掷骰前检查
        if not self._cooldown_ok(key, GROUP_COOLDOWN_HOURS * 3600):
            return

        # 判定硬冷却：30分钟内判定过（无论中没中）就不再判定
        if not self._judge_ok(key):
            return

        # 兴趣值门槛：对这个群有兴趣才主动
        try:
            interest = await subheartflow.interest_chatting.get_interest()
        except Exception as e:
            logger.error(f"获取兴趣值失败({stream_id}): {e}")
            return
        if interest < GROUP_INTEREST_THRESHOLD:
            logger.debug(f"[主动-群] {stream_id} 兴趣值 {interest:.1f} < 门槛 {GROUP_INTEREST_THRESHOLD}，跳过")
            return

        last_any_ts, last_self_ts, _ = self._query_last_message_times(stream_id)
        now = time.time()

        # 时机因子
        if last_self_ts and (now - last_self_ts) < GROUP_SELF_TALKED_MINUTES * 60:
            factor = GROUP_SELF_TALKED_FACTOR  # 刚说过话，别刷存在感
            factor_note = "刚说过话"
        elif last_any_ts and (now - last_any_ts) > GROUP_COLD_HOURS * 3600:
            factor = GROUP_COLD_FACTOR  # 冷场久了
            factor_note = "群里冷场"
        else:
            factor = 1.0
            factor_note = "平常"

        miss_streak = self._group_miss_streak.get(stream_id, 0)
        bonus = min(GROUP_MISS_BONUS_MAX, miss_streak * GROUP_MISS_BONUS_STEP)
        hitline = min(100, GROUP_BASE_LINE * factor + bonus)

        roll = random.randint(1, 100)
        self._last_judge[key] = now  # 判定发生（无论中没中），写判定硬冷却时间戳
        logger.info(
            f"[主动-群] {stream_id} 掷骰 {roll} vs 命中线 {hitline:.0f}"
            f"（基线{GROUP_BASE_LINE}×{factor_note}{factor} + 没中加成{bonus}）兴趣={interest:.1f}"
        )

        if roll > hitline:
            self._group_miss_streak[stream_id] = miss_streak + 1
            return

        self._group_miss_streak[stream_id] = 0
        self._last_hit[key] = now

        if DRY_RUN_GROUP:
            logger.info(f"[主动-群] 试运行模式命中 {stream_id}，不真发。开启真发请改 proactive_manager.DRY_RUN_GROUP=False")
            return

        # 统一闲逛会话：LLM 自主决定上上网干什么（刷空间/分享/发说说/找人聊…）
        # 旧固定路由 _send_group_proactive 保留在下方可随时回退
        await self._idle_browse_session()

    async def _send_group_proactive(self, stream_id: str) -> None:
        """群聊主动话题：LLM 生成 → 直接发送"""
        from ..plugins.chat.chat_stream import chat_manager
        from ..plugins.utils.chat_message_builder import (
            build_readable_messages,
            get_raw_msg_before_timestamp_with_chat,
        )
        from ..plugins.chat.message import MessageSending
        from ..plugins.storage.storage import MessageStorage
        from maim_message import UserInfo, Seg
        from ..individuality.individuality import Individuality
        from ..plugins.moods.moods import MoodManager
        from ..config.config import global_config as cfg

        chat_stream = chat_manager.get_stream(stream_id)
        if not chat_stream:
            logger.warning(f"[主动-群] {stream_id} 拿不到 chat_stream，放弃")
            return

        # 上下文 + 人设 + 心情
        context_msgs = get_raw_msg_before_timestamp_with_chat(
            chat_id=stream_id, timestamp=time.time(), limit=15
        )
        chat_log = await build_readable_messages(
            context_msgs, replace_bot_name=True, merge_messages=True, timestamp_mode="relative", read_mark=0.0
        )
        personality = Individuality.get_instance().get_prompt(x_person=2, level=2)
        mood_prompt = MoodManager.get_instance().get_prompt()
        time_now = time.strftime("%Y-%m-%d %H:%M", time.localtime())

        prompt = f"""你是群聊里的一个普通群友。你的特点：{personality}
{mood_prompt}
现在是 {time_now}。

最近的群聊记录：
{chat_log}

你过了一阵子想主动找个话题跟大家聊。但先判断时机：如果现在主动开口不合时宜（群里正在吵架/气氛沉重不适合插科打诨/话题敏感到不该插嘴），只输出 SKIP 两个字，什么都不要说。
时机合适的话就开口，要求：
1. 像平时说话一样直接开口，禁止"在吗""大家好""有人吗"这类打招呼式开场。
2. 可以从最近的聊天内容自然延伸，也可以聊你自己的日程、见闻、想法。
3. {TEXT_MAX_LEN}字以内，口语化，可以不完整。只输出消息内容本身，不要任何前后缀。"""

        from ..plugins.models.utils_model import LLMRequest

        # 主动话题生成：用 32B 档保证文风（llm_summary 同款），不用主力回复模型
        llm = LLMRequest(model=cfg.llm_summary, request_type="proactive", temperature=0.8)
        response, _, _ = await llm.generate_response(prompt)
        text = (response or "").strip().strip('"').strip("'")
        if not text or text.upper() == "SKIP":
            logger.info(f"[主动-群] {stream_id} LLM 判断当前时机不适合插话（{text or '空响应'}），放弃")
            return
        if len(text) > TEXT_MAX_LEN * 2:
            text = text[: TEXT_MAX_LEN * 2]

        bot_user_info = UserInfo(
            user_id=cfg.BOT_QQ, user_nickname=cfg.BOT_NICKNAME, platform=chat_stream.platform
        )
        message = MessageSending(
            message_id=f"proactive{round(time.time(), 2)}",
            chat_stream=chat_stream,
            bot_user_info=bot_user_info,
            sender_info=None,
            message_segment=Seg(type="text", data=text),
            reply=None,
            is_head=False,
            is_emoji=False,
            thinking_start_time=time.time(),
        )
        await message.process()
        from ..message.api import global_api

        await global_api.send_message(message)
        await MessageStorage().store_message(message, chat_stream)
        logger.info(f"[主动-群] {stream_id} 主动发言已发送: {text[:50]}")

    # ================= 私聊版 =================
    async def _roll_private(self, conversation) -> None:
        stream_id = conversation.stream_id
        key = f"p:{stream_id}"

        if not self._cooldown_ok(key, PRIVATE_COOLDOWN_MINUTES * 60):
            return

        # 判定硬冷却：30分钟内判定过（无论中没中）就不再判定
        if not self._judge_ok(key):
            return

        last_any_ts, last_self_ts, last_other_ts = self._query_last_message_times(stream_id)
        now = time.time()

        # 时机因子（speak-up 语义：距「她上次说话」多久——Moui自己上次发言，不是对方）
        if last_self_ts and (now - last_self_ts) < PRIVATE_RECENT_MINUTES * 60:
            factor = PRIVATE_RECENT_FACTOR  # 刚聊完，别追着说
            factor_note = "刚聊过"
        elif last_self_ts and (now - last_self_ts) > PRIVATE_LONG_HOURS * 3600:
            factor = PRIVATE_LONG_FACTOR  # 冷太久，有点想TA
            factor_note = "很久没聊"
        else:
            factor = 1.0
            factor_note = "平常"

        miss_streak = self._private_miss_streak.get(stream_id, 0)
        bonus = min(PRIVATE_MISS_BONUS_MAX, miss_streak * PRIVATE_MISS_BONUS_STEP)
        hitline = min(100, PRIVATE_BASE_LINE * factor + bonus)

        # 未回应抑制（拟真：真人连着找几次对方都没反应就识趣了，不会无限输出）：
        # 上次主动找TA之后对方一条都没说 → 命中线砍到 3 成；连续抑制靠 _last_hit 与 last_other_ts 的对比自然成立
        last_hit_ts = self._last_hit.get(key, 0.0)
        if last_hit_ts and (not last_other_ts or last_other_ts < last_hit_ts):
            hitline *= 0.3
            factor_note += "·上次主动后TA一直没回应"

        roll = random.randint(1, 100)
        self._last_judge[key] = now  # 判定发生（无论中没中），写判定硬冷却时间戳
        logger.info(
            f"[主动-私聊] {stream_id} 掷骰 {roll} vs 命中线 {hitline:.0f}"
            f"（基线{PRIVATE_BASE_LINE}×{factor_note}{factor} + 没中加成{bonus}）"
        )

        if roll > hitline:
            self._private_miss_streak[stream_id] = miss_streak + 1
            return

        self._private_miss_streak[stream_id] = 0
        self._last_hit[key] = now

        if DRY_RUN_PRIVATE:
            logger.info(f"[主动-私聊] 试运行模式命中 {stream_id}，不真注入。开启真发请改 proactive_manager.DRY_RUN_PRIVATE=True")
            return

        # 统一闲逛会话：LLM 自主决定（内含找不找TA聊的时机判断）
        # 旧路径 _private_llm_gate + _inject_private_goal 保留在下方可随时回退
        await self._idle_browse_session()

        # ---- 旧固定路径（回退用）：直接注入"主动找TA"目标 ----
        # if not await self._private_llm_gate(stream_id):
        #     logger.info(f"[主动-私聊] {stream_id} LLM 复核判断现在找TA不合适，放弃")
        #     return
        # self._inject_private_goal(conversation)

    async def _private_llm_gate(self, stream_id: str) -> bool:
        """私聊命中后的 LLM 复核：带最近聊天记录+当前时间+人设，判断现在主动找TA是否合适。

        返回 True=放行注入目标；False=放弃。复核出错时放行（公式已命中，不白跑）。
        """
        try:
            from ..plugins.chat.chat_stream import chat_manager
            from ..plugins.utils.chat_message_builder import (
                build_readable_messages,
                get_raw_msg_before_timestamp_with_chat,
            )
            from ..config.config import global_config as cfg
            from ..individuality.individuality import Individuality
            from ..plugins.models.utils_model import LLMRequest

            chat_stream = chat_manager.get_stream(stream_id)
            if not chat_stream:
                return True  # 拿不到上下文就放行

            context_msgs = get_raw_msg_before_timestamp_with_chat(
                chat_id=stream_id, timestamp=time.time(), limit=15
            )
            if not context_msgs:
                return True
            chat_log = await build_readable_messages(
                context_msgs, replace_bot_name=True, merge_messages=True, timestamp_mode="relative", read_mark=0.0
            )
            time_now = time.strftime("%Y-%m-%d %H:%M", time.localtime())
            personality = Individuality.get_instance().get_prompt(x_person=2, level=1)

            prompt = f"""你是{cfg.BOT_NICKNAME}。{personality}
现在是 {time_now}。

你和这位朋友的最近聊天记录：
{chat_log}

你正打算主动找TA说句话。判断一下时机：
- 如果现在找TA很自然（有话题没聊完/就是有一阵子没说话了想聊聊/有想分享的东西），输出 GO
- 如果现在找不合适（上次聊天刚闹得不愉快/今天已经找过TA好几次/深夜3点找普通朋友闲聊很怪），输出 SKIP
只输出 GO 或 SKIP。"""
            # 免费判断模型（Xing4.0-29B），零成本
            llm = LLMRequest(model=cfg.llm_topic_judge, request_type="proactive_gate", temperature=0.3, max_tokens=10)
            response, _, _ = await llm.generate_response(prompt)
            verdict = (response or "").strip().upper()
            logger.debug(f"[主动-私聊] {stream_id} LLM 复核结果: {verdict}")
            return "SKIP" not in verdict
        except Exception as e:
            logger.error(f"私聊LLM复核异常: {e}")
            return True

    # ================= 闲逛会话（统一上网冲浪：LLM 持续循环直到 DONE） =================
    IDLE_MAX_ROUNDS = 6
    IDLE_SYSTEM_TEMPLATE = """你是一个AI聊天机器人{bot_name}。{personality}
{mood_prompt}
{quota_hint}
现在是 {time_now}。

你刚刚闲下来了，打算上上网打发时间。这是一个上网会话：每一步调用一个工具执行动作，看完结果再决定下一步，逛够了就直接回复 DONE（不调用任何工具）。

可做的动作：
·browse_qzone：刷刷空间，看朋友们最近发了什么
·like_qzone：给看到的说说点赞
·comment_qzone：给看到的说说留句评论
·post_qzone：自己有感触就发条空间
·change_signature：想换个心情就改个签
·send_chat_message：把想说的/刷到的有趣东西分享到某个群或私聊
·web_search：上网查点东西

规则：
·分享/发言要像平时说话的样子，禁止"在吗""来看看这个"这种干巴巴的开场
·发空间/评论/个签要贴合你现在的真实心情，没有感触别硬发
·逛了一圈没想干的就直接 DONE，不硬凑
·最多 {max_rounds} 步，一步一步来"""

    IDLE_TOOLS = [
        {
            "type": "function",
            "function": {
                "name": "browse_qzone",
                "description": "刷刷空间，看看认识的人最近发的说说",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "like_qzone",
                "description": "给一条说说点赞（owner_uin 和 tid 从 browse_qzone 的结果里拿）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "owner_uin": {"type": "string"},
                        "tid": {"type": "string"},
                    },
                    "required": ["owner_uin", "tid"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "comment_qzone",
                "description": "给一条说说留句评论，或回复那条说说下的某条评论（楼中楼，像平时说话，一天别评太多）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "owner_uin": {"type": "string"},
                        "tid": {"type": "string"},
                        "content": {"type": "string", "description": "评论内容，50字内"},
                        "comment_id": {
                            "type": "string",
                            "description": "可选。要回复的那条评论的 comment_id（browse_qzone 结果里评论信息带着）",
                        },
                        "appid": {"type": "string", "description": "说说类型id，默认311"},
                    },
                    "required": ["owner_uin", "tid", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "post_qzone",
                "description": "发一条自己的空间说说（纯文字，200字内，贴合当下心情）",
                "parameters": {
                    "type": "object",
                    "properties": {"content": {"type": "string"}},
                    "required": ["content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "change_signature",
                "description": "改自己的QQ个性签名（100字内）",
                "parameters": {
                    "type": "object",
                    "properties": {"signature": {"type": "string"}},
                    "required": ["signature"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "send_chat_message",
                "description": "把想说的/刷到的有趣东西发到某个群或私聊（target 填群号或好友QQ号，从会话开头的聊天对象列表里选）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "群号或好友QQ号"},
                        "content": {"type": "string", "description": "要说的内容，像平时说话"},
                    },
                    "required": ["target", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "上网搜点东西（新梗/新闻/任何想查的）",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        },
    ]

    async def _idle_browse_session(self) -> None:
        """闲逛会话：function calling 持续循环，LLM 自主决定上上网干什么，直到 DONE 或轮数上限。"""
        if self._idle_running:
            logger.debug("[闲逛] 已有会话进行中，跳过")
            return
        # 全局闲逛冷却：刚逛过（1小时内）不再开新会话——闲逛是全局单例引擎，不是每个群各逛各的
        if getattr(self, "_idle_cooldown_until", 0) > time.time():
            logger.debug("[闲逛] 冷却中（上次会话结束不到1小时），跳过")
            return
        self._idle_running = True
        try:
            from ..plugins.models.utils_model import LLMRequest
            from ..config.config import global_config as cfg
            from ..individuality.individuality import Individuality
            from ..plugins.moods.moods import MoodManager
            from ..plugins.moods.quota_hint import build_quota_hint

            llm = LLMRequest(
                model=cfg.llm_normal,
                temperature=cfg.llm_normal.get("temp", 0.2),
                max_tokens=500,
                request_type="idle_browse",
                enable_chat_history=False,
            )
            personality = Individuality.get_instance().get_prompt(x_person=2, level=2)
            mood_prompt = MoodManager.get_instance().get_prompt()
            time_now = time.strftime("%Y-%m-%d %H:%M", time.localtime())

            targets = self._list_chat_targets()
            if targets["groups"] or targets["friends"]:
                targets_text = (
                    "群：" + "、".join(targets["groups"]) + "；私聊好友：" + "、".join(targets["friends"])
                )
            else:
                targets_text = "（暂无可分享对象）"

            system_text = self.IDLE_SYSTEM_TEMPLATE.format(
                bot_name=cfg.BOT_NICKNAME,
                personality=personality,
                mood_prompt=mood_prompt,
                quota_hint=build_quota_hint(),
                time_now=time_now,
                max_rounds=self.IDLE_MAX_ROUNDS,
            )
            user_text = f"上网吧，想干嘛干嘛。可以分享的对象：{targets_text}"
            messages = [
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_text},
            ]
            session_state: Dict[str, int] = {"sent_count": 0}

            for _round in range(self.IDLE_MAX_ROUNDS):
                payload = {
                    "model": llm.model_name,
                    "messages": messages,
                    "tools": self.IDLE_TOOLS,
                    # 验尸修正：LLMRequest 不把 temperature 存成实例属性（进 params），读属性必炸
                    "temperature": float(cfg.llm_normal.get("temp", 0.2) or 0.2),
                }
                response = await llm._execute_request(endpoint="/chat/completions", payload=payload, prompt=user_text)

                if len(response) == 3:
                    content, _reasoning, tool_calls = response
                    if not tool_calls:
                        # 不调工具 = 结束（DONE 或自言自语）
                        logger.info(f"[闲逛] 会话结束: {(content or '')[:60]}")
                        break
                    messages.append({"role": "assistant", "content": content or "", "tool_calls": tool_calls})
                    for call in tool_calls:
                        result_text = await self._execute_idle_action(call, session_state)
                        messages.append(
                            {"role": "tool", "tool_call_id": call.get("id", ""), "content": result_text}
                        )
                    continue
                else:
                    break
            logger.info("[闲逛] 上网会话关闭")
        except Exception as e:
            logger.error(f"闲逛会话异常: {e}")
        finally:
            self._idle_running = False
            # 全局闲逛冷却：一次会话结束后 1 小时内不再开新会话（只有一个闲逛引擎，别频繁开）
            self._idle_cooldown_until = time.time() + 3600

    async def _execute_idle_action(self, call: dict, session_state: Dict[str, int]) -> str:
        """闲逛会话内的动作分发"""
        name = call.get("function", {}).get("name", "")
        try:
            args = json.loads(call.get("function", {}).get("arguments") or "{}")
        except json.JSONDecodeError:
            return "参数解析失败"
        try:
            if name == "browse_qzone":
                return await self._idle_browse_qzone()
            if name == "like_qzone":
                return await self._idle_qzone_write("like", args)
            if name == "comment_qzone":
                return await self._idle_qzone_write("comment", args)
            if name == "post_qzone":
                return await self._idle_qzone_write("post", args)
            if name == "change_signature":
                return await self._idle_change_signature(str(args.get("signature", "")))
            if name == "send_chat_message":
                return await self._idle_send_chat_message(
                    str(args.get("target", "")), str(args.get("content", "")), session_state
                )
            if name == "web_search":
                from ..do_tool.tool_can_use.web_search_tool import WebSearchTool

                result = await WebSearchTool().execute({"query": str(args.get("query", ""))})
                return str(result.get("message") or result.get("error") or "没搜到")
            return f"未知动作: {name}"
        except Exception as e:
            logger.error(f"闲逛动作 {name} 执行失败: {e}")
            return f"动作执行失败: {e}"

    def _list_chat_targets(self) -> Dict[str, List[str]]:
        """枚举可分享的聊天对象（群+私聊好友）"""
        groups: List[str] = []
        friends: List[str] = []
        try:
            from ..plugins.chat.chat_stream import chat_manager

            for s in list(chat_manager.streams.values()):
                g = getattr(s, "group_info", None)
                u = getattr(s, "user_info", None)
                if g is not None:
                    gid = str(getattr(g, "group_id", ""))
                    gname = getattr(g, "group_name", "") or gid
                    if gid:
                        groups.append(f"{gname}({gid})")
                elif u is not None:
                    uid = str(getattr(u, "user_id", ""))
                    uname = getattr(u, "user_nickname", "") or uid
                    if uid:
                        friends.append(f"{uname}({uid})")
        except Exception as e:
            logger.debug(f"枚举聊天对象失败: {e}")
        return {"groups": groups[:6], "friends": friends[:6]}

    def _get_friend_uins(self, limit: int = 5) -> List[str]:
        """从 MongoDB 私聊消息流提取最近聊过的好友 QQ（按最近活跃排序，重启不丢名单）"""
        try:
            from ..common.database import db

            week_ago = time.time() - 7 * 86400
            cursor = db.messages.aggregate(
                [
                    {"$match": {"chat_info.group_info": None, "time": {"$gte": week_ago}}},
                    {"$group": {"_id": "$chat_info.user_info.user_id", "last": {"$max": "$time"}}},
                    {"$sort": {"last": -1}},
                    {"$limit": limit},
                ]
            )
            return [str(d["_id"]) for d in cursor if d.get("_id")]
        except Exception as e:
            logger.error(f"提取私聊好友列表失败: {e}")
            return []

    async def _idle_browse_qzone(self) -> str:
        """刷空间：拉认识的人的最新说说"""
        from ..plugins.qzone.qzone_client import get_qzone_client
        from ..config.config import global_config as cfg

        client, err = get_qzone_client(bot_qq=str(cfg.BOT_QQ))
        if not client:
            return f"空间打不开：{err}"
        uins = self._get_friend_uins(limit=5)
        if not uins:
            return "空间里暂时没刷到认识的人的动态"
        result = await asyncio.to_thread(client.get_friend_feeds, uins, 1)
        feeds = result.get("feeds") or []
        if not feeds:
            return "刷了一圈空间，朋友们最近都没更新（或对方空间设置了权限）"
        lines = [
            f"{i + 1}. [{f['owner_uin']}]{f['time']}: {f['content']}（tid:{f['tid']}）"
            for i, f in enumerate(feeds)
        ]
        return "刷到这些动态（owner_uin/tid 用于点赞评论）：\n" + "\n".join(lines)

    async def _idle_qzone_write(self, kind: str, args: dict) -> str:
        """空间写动作（点赞/评论/发说说）：DRY_RUN_GROUP 拦截 + 各自冷却"""
        if DRY_RUN_GROUP:
            return "（试运行模式：对外动作只记日志不真执行）"
        from ..plugins.qzone.qzone_client import get_qzone_client
        from ..config.config import global_config as cfg

        client, err = get_qzone_client(bot_qq=str(cfg.BOT_QQ))
        if not client:
            return f"空间打不开：{err}"

        if kind == "like":
            result = await asyncio.to_thread(
                client.like_feed, str(args.get("owner_uin", "")), str(args.get("tid", ""))
            )
        elif kind == "comment":
            from ..do_tool.tool_can_use.qzone_tool import _check_comment_cooldown, _set_comment_cooldown

            ok, why = _check_comment_cooldown()
            if not ok:
                return why
            result = await asyncio.to_thread(
                client.comment_feed,
                str(args.get("owner_uin", "")),
                str(args.get("tid", "")),
                str(args.get("content", "")),
                comment_id=str(args.get("comment_id", "") or ""),
                appid=str(args.get("appid", "311") or "311"),
            )
            if result.get("success"):
                _set_comment_cooldown()
        else:  # post
            from ..do_tool.tool_can_use.qzone_tool import _check_cooldown, _set_cooldown

            ok, why = _check_cooldown()
            if not ok:
                return why
            result = await asyncio.to_thread(client.publish_mood, str(args.get("content", "")))
            if result.get("success"):
                _set_cooldown()
        return result.get("message") or result.get("error") or "完成"

    async def _idle_change_signature(self, signature: str) -> str:
        """改个签：复用工具类（走完整冷却+调用）"""
        if not signature.strip():
            return "签名内容为空"
        from ..do_tool.tool_can_use.change_signature_tool import ChangeSignatureTool

        result = await ChangeSignatureTool().execute({"signature": signature.strip()})
        if result.get("status") == "ok":
            return "签名改好了"
        return f"签名没改成功: {result.get('message', '')}"

    async def _idle_send_chat_message(self, target: str, content: str, session_state: Dict[str, int]) -> str:
        """把内容分享到指定群/私聊（每会话限 2 条防刷屏）"""
        from ..plugins.chat.chat_stream import chat_manager
        from ..plugins.chat.message import MessageSending
        from ..message.api import global_api
        from ..storage.storage import MessageStorage
        from maim_message import UserInfo, Seg
        from ..config.config import global_config as cfg

        target = str(target).strip()
        content = str(content).strip()
        if not target or not content:
            return "target 或 content 为空"
        if session_state.get("sent_count", 0) >= 2:
            return "这次闲逛已经分享过两条了，别刷屏"

        stream = None
        for s in list(chat_manager.streams.values()):
            g = getattr(s, "group_info", None)
            u = getattr(s, "user_info", None)
            if g is not None and str(getattr(g, "group_id", "")) == target:
                stream = s
                break
            if u is not None and str(getattr(u, "user_id", "")) == target:
                stream = s
                break
        if not stream:
            return f"没找到 {target} 对应的聊天（对方最近没跟你说过话？）"

        is_group = getattr(stream, "group_info", None) is not None
        if is_group and DRY_RUN_GROUP:
            return "（试运行模式：群消息没真发）"

        bot_user_info = UserInfo(
            user_id=cfg.BOT_QQ, user_nickname=cfg.BOT_NICKNAME, platform=stream.platform
        )
        message = MessageSending(
            message_id=f"idle{round(time.time(), 2)}",
            chat_stream=stream,
            bot_user_info=bot_user_info,
            sender_info=None,
            message_segment=Seg(type="text", data=content[:100]),
            reply=None,
            is_head=False,
            is_emoji=False,
        )
        # 手动给入库文本（不走 process()，回看上下文/记忆提炼要知道自己发过什么）
        message.processed_plain_text = content[:100]
        message.detailed_plain_text = content[:100]
        await global_api.send_message(message)
        await MessageStorage().store_message(message, stream)
        session_state["sent_count"] = session_state.get("sent_count", 0) + 1
        logger.info(f"[闲逛] 已分享到 {'群' if is_group else '私聊'} {target}: {content[:50]}")
        return "消息发出去了"

    def _inject_private_goal(self, conversation) -> None:
        """私聊注入：往 PFC 会话 goal_list 塞"主动找TA"目标，PFC planner 下一轮自然执行"""
        try:
            goal_list = getattr(conversation.conversation_info, "goal_list", None)
            if goal_list is None:
                logger.warning("[主动-私聊] conversation_info.goal_list 不可用，放弃注入")
                return
            # 防重复：已有主动目标就不叠
            for item in goal_list:
                if isinstance(item, dict) and "主动找" in str(item.get("goal", "")):
                    logger.debug("[主动-私聊] 已存在主动搭话目标，跳过")
                    return
            goal_list.append(
                {
                    "goal": "主动找TA聊几句，可以分享一件最近的小事、问个问题，或者接着之前的话题",
                    "reasoning": "有一阵子没说话了，想主动找TA说说话",
                }
            )
            logger.info(f"[主动-私聊] {conversation.stream_id} 已注入主动搭话目标，PFC 将自行规划开场")
        except Exception as e:
            logger.error(f"注入私聊主动目标失败: {e}")

    # ================= 总调度 =================
    async def roll_all(self) -> None:
        """一轮掷骰：群聊 + 私聊"""
        if _in_quiet_hours():
            logger.debug("[主动] 安静时段（1-8点），跳过本轮掷骰")
            return

        # 群聊：遍历所有子心流
        if self.subheartflow_manager:
            try:
                subheartflows = self.subheartflow_manager.get_all_subheartflows()
                for shf in list(subheartflows):
                    try:
                        await self._roll_group(shf)
                    except Exception as e:
                        logger.error(f"群聊掷骰异常({getattr(shf, 'subheartflow_id', '?')}): {e}")
            except Exception as e:
                logger.error(f"群聊掷骰调度失败: {e}")

        # 私聊：遍历所有 PFC 会话
        try:
            from ..plugins.PFC.pfc_manager import PFCManager

            instances = dict(PFCManager.get_instance()._instances or {})
            for conversation in instances.values():
                try:
                    if getattr(conversation, "should_continue", False):
                        await self._roll_private(conversation)
                except Exception as e:
                    logger.error(f"私聊掷骰异常({getattr(conversation, 'stream_id', '?')}): {e}")
        except Exception as e:
            logger.error(f"私聊掷骰调度失败: {e}")

    async def run_forever(self, interval: float = ROLL_INTERVAL_MIN) -> None:
        """主循环：随机节奏（15-40分钟一轮）+ 每对话判定硬冷却 30 分钟"""
        logger.info(
            f"主动搭话管理器已启动 随机周期:{ROLL_INTERVAL_MIN}-{ROLL_INTERVAL_MAX}s "
            f"判定冷却:{JUDGE_COOLDOWN_SECONDS}s | 群聊:{'试运行(只记日志)' if DRY_RUN_GROUP else '真发'} | 私聊:{'试运行(只记日志)' if DRY_RUN_PRIVATE else '真发'}"
        )
        while True:
            await asyncio.sleep(random.uniform(ROLL_INTERVAL_MIN, ROLL_INTERVAL_MAX))
            try:
                await self.roll_all()
            except Exception as e:
                logger.error(f"主动搭话周期异常: {e}")


proactive_manager = ProactiveManager.get_instance()
