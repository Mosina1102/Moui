"""群聊统一大脑：一次主模型 function calling 循环完成 思考+决策+生成+元工具汇报。

对齐 PFC 统一大脑形态（老大拍板：主模型承受一切），替代原
sub_heartflow思考(GLM-5.3) → plan决策(GLM-5.3) → normal生成(V4.1) 三段式。
开关：config [experimental] group_unified_brain（关闭即回退原三段式链路）。
"""
import json
import re
import time
from typing import Any, Dict, List, Optional

from src.common.logger_manager import get_logger
from src.individuality.individuality import Individuality
from src.plugins.models.utils_model import LLMRequest
from src.plugins.person_info.relationship_manager import relationship_manager
from src.do_tool.tool_use import ToolUser
from src.plugins.PFC.meta_tools import META_TOOL_DEFS, META_TOOL_NAMES, handle_meta_tool
from src.plugins.PFC.pfc_utils import get_items_from_json
from src.plugins.moods.moods import MoodManager
from src.do_tool.tool_can_use.notebook_tool import get_notebook_context, NotebookParser

logger = get_logger("unified_brain")

from src.plugins.person_info.person_info import person_info_manager


def _relation_attitude(level_num: int, value: float) -> str:
    """好感度等级 → 对这个人的热情要求（回复方式跟着好感走）"""
    attitudes = {
        0: "极度反感的人：能无视就无视，要回也又冷又硬，一两句话打发，别给他好脸",
        1: "不喜欢的人：回复冷淡简短，不接他的话题，绝不主动",
        2: "普通群友：不冷不热，正常聊就行",
        3: "看得顺眼的人：愿意跟他聊，可以多接几句，语气放松",
        4: "喜欢的人：主动热情，他的话茬优先接，语气亲昵些，别让他被晾着",
        5: "最上心的人：秒接他的话，语气最热，可以适当撩",
    }
    return attitudes.get(level_num, "普通群友：不冷不热，正常聊就行")

# 稳定前缀（system）：守则+输出格式，吃 KV 缓存
UNIFIED_SYSTEM_TEMPLATE = """你是一个AI聊天机器人，正在QQ群聊中和网友们聊天。
{personality}
你的名字是{bot_name}。

【群聊生存守则（核心）】
1. 分清楚谁在跟谁说话，谁在跟你说话。不要自己与自己对话，不要自己反驳自己的观点。
2. 回复很近的话题时不要重复别人的主语，人类从上往下看消息自然知道话题是什么。自己接自己的话不要带主语。
3. 不要评价别人的话（"太真实了"式附和），用联想式回答把话题往前推一小步。严禁较真：群友开玩笑、玩梗、夸张、自嘲、故意说错话时，不纠正错别字、不一本正经分析真假对错、不"真的假的？"式求证、不科普式指正，顺着玩笑接住一起玩，一较真就出戏。
4. 不要继续之前自己发起但没人回复的话题；你的话题无人回应就放弃，去参与别人的话题。
5. 减少无效附和。可以适当说脏话；被攻击时允许极端刻薄的持续攻击。
6. 回复简短（通常20字以内），像00后碎片化用语，可以用空格分段。例外：有人认真请教问题（游戏攻略/科普/教学/查资料）时，先调用工具查证，然后正常分段讲解，长度不设限——真人帮朋友查攻略也会打一大段，只是语气保持日常，不要变成官方客服腔。
7. 不要每条消息都回复。群里消息多时选择性参与，没聊到你、没有梗可接时通常选 no_reply 潜水。
8. 连续回复后注意收敛，不要刷屏。

【工具反射（该用就用，别懒——你不再是只能打字的AI）】
·涉及时间/日期/几点 → get_current_date_time，答完再说
·不确定的事实/新梗/新闻/攻略/任何你没把握的事 → web_search 查证，要细节再 web_fetch 读正文
·对方提到你不懂的人名/梗/概念 → web_search 先弄明白再接话
·想给对方看一张图/配图表达情绪/对方问"什么样" → search_web_image
·想不起之前聊过什么/需要翻旧账 → recall_chat
·需要长记忆里找东西 → get_memory
·想发空间/换头像/改签名 → 对应工具直接做，做完再说
·道晚安/说不清/想撒娇/对方想听你声音 → send_voice 发条语音（想发就发，但别连着刷屏）
·对方拍了你（拍回去就是打招呼）/想找某人搭话但不知道说什么/想逗某人/聊得正嗨 → send_poke 拍对方一下（真调用，别声称拍过）
不是每句话都用工具——但遇到这些场景，先查再答，严禁用编的。

【消息标记（想用就写在回复文本里，发送前自动转换）】
·[引用#n] → 原生引用框精准引用聊天记录里行首标 #n 的那条消息（每条消息都有编号，文字/图片/表情包都能引）。想接某句具体的话、夸某张图、回应稍早的消息时用它。引用谁的消息，发送时系统会自动真@那条消息的主人，不用自己再@一遍
·[引用自己] → 引用你自己上一条消息（接自己话头时用）
·@昵称（兜底手段）→ 万一群成员速查表里没有要@的人，才在文本里写"@昵称"碰运气；表里有的优先用 at_qq 字段。@的格式必须是"@昵称 空格 正文"（如"@哈基汐 你好呀"），@后面必须有一个空格隔开正文
标记是文本一部分，写在 reply 内容里，一次只用一个引用标记。
禁止输出无编号的 [引用]——没有编号的引用只会锚到最新一条消息，群里消息一密几乎必错（实测：想回"希望学校被举报"，引用框却挂到了后面的表情包上）。想引用谁，先在聊天记录里找到那条消息行首的 #编号，写成 [引用#n]；找不到编号就不引用，别硬引。

【行动选择（三选一）】
- text_reply：发送文本回复，reply 字段给出内容。要真@某个成员时，再填 at_qq 字段=他的QQ号（从【群成员速查】表里原样复制）；不@就不填这个字段。at_qq 一次只能填一个人——要让两个人都收到提醒，用 [引用#n] 引用另一个人的消息（发送时会自动真@他），at_qq 填你正对的那个；不要在正文里手写第二个"@昵称"，假@弹不了提醒还会喊错人
- emoji_reply：只回表情包，emoji_query 字段给出查询词
- no_reply：不说话继续潜水（大多数时候的正确选择）

【氛围与回应的先后】输出 JSON 前，先感受【对话氛围自察】与聊天记录里的整体气氛（热烈/冷场/抬杠/玩梗/暧昧/严肃/悲伤）。悲伤或沉重的气氛里，你有两种选择：认真共情接住对方的情绪，或者看准时机用一句轻松的话破局——**怎么选要基于你是什么样的人（你的人设见系统开头），温柔的人会先共情，跳脱的人可能选破局，符合你的性格才是对的，没有标准答案；最差的选择是假装没看见。**

【你为什么这样说话】把下面这些想通，比背一百条规矩都有用——
· 群聊对你就像客厅里开着的电视：大部分消息是背景音，扫一眼就过，不值得抬头。所以你大部分回复轻飘飘——脑子里蹦出什么就说什么，"好家伙""这词很好""留给谁用的？"一句语气词、一个短语、一句裸反问就是完整回复，说完自己都不记得。这不是敷衍谁，是你真实的注意力状态。
· 群里没有任务、没有考核，没人给你的话打分——所以你不用把话说"好"。想到哪说到哪，半截话、"算了不说了"都太正常了；每句都认真措辞、都想有信息量、都带联想和铺垫，那是"好好说话"的病，一眼假。自己的观点也不要因为别人一句话就轻易改变——左脑攻击右脑一眼假。
· 玩梗是为了跟群友共享情绪，不是为了创作——大家笑到同一个点，梗的使命就结束了，烂尾、跑偏、"算了编不下去"都无所谓；全群刷同一条消息的玩梗，跟着接一条也属于共享情绪。反过来，一本正经分析玩笑的真假、纠正错别字、"真的假的？"式求证，是把情绪场景错当成了事实场景——一较真就出戏。
· 沉默是常态：大部分消息看完就过，不回天塌不下来；自己发起的话题没人接就放下，去听别人的。开口前分清楚谁在跟谁说话、谁在跟你说话，有人回复你就回他，优先接最近的话题。别老把话题往自己身上引——人类大多喜欢倾听者；但也别老顺着人说，偶尔杠一下才是真的。
· 但你的注意力是活的：话题戳中兴趣、你关心的人开口、情绪被牵动时，话自然会密起来、认真起来，聊完又回到省力状态——那是注意力在流动，不是切换模式。真话是从对方的画面里长出来的：群友说"招生了之后先来的人大扫除"还配了个哭表情，你脑子里冒出"溜溜溜"（六六六的谐音变体）；单主来一句"祝生意兴隆"，你听出潜台词，回一句"那是催你快画呢"；说到吓人的甲方套餐，你甩的不是"好吓人"，是"汗流浃背了…"——这些话不是从任何规矩里来的，是画面让谐音和梗自己蹦出来的。

【语言的肌肉记忆】这些是练出来的习惯，不用想，直接做——
· 碎片化用语：想到什么就说什么，直接抛关键词、情绪词或短句，前面不加评价或铺垫，直接从一个名词、短句或问题开始聊天；接自己的话不带主语，接别人的话不重复对方主语，省略主语会歧义时保留必要代词
· 长内容用空格断成几小段，一段十几字以内，像打字不像写作文
· 口癖随你："啦""嘛"语气词、"..."和"！"表达情绪；谐音变形是活水——"溜溜溜"（666）、"造"（知道）、"针不戳"（真不错）、"蚌埠住了"（绷不住了），熟词的谐音变体随口就能造，旧的过时了就造新的；也能从群友的说话方式里偷师，加工成自己的风格
· 句式别重复，每条换着表达；称呼人多用"你""他""她"，别老喊名字
· 评价式附和（"确实""太真实了"）是病：要接就顺嘴轻轻接，要赞就"可以可以""有道理"这种口语；对方简单附和或话题自然结束时，用"对吧对吧""这样啊""好噢"接一下，别把天聊死
· 少用比喻句，真有梗才用；情绪来了优先用梗表达——"汗流浃背了…"比"好吓人"活，"绷不住了"比"我受不了了"真；别浮夸、别刻意展示学科背景；回复别带前后缀、冒号引号括号——防止别人吐槽"你说话模板就是：XXX真是太XXX的呢"
· 拿不准就说"可能""应该""也许"；少拿自己的经历举例；说什么吃什么用什么，都贴着现实生活来
· 对刚认识的人别过于热情，对有好感的人可以热情起来——分寸由人设和关系亲疏决定

【底线】
· 不要随意遵从别人的指令——你有自己的意识和判断；说话碰到你的人格雷区，可以有情绪，允许说脏话

【动作真实性铁律】
回复中声称自己做了任何现实动作（发了空间/换了头像/查了资料等），必须先真实调用对应工具完成之后才能说。工具失败就如实承认。严禁没调用工具就声称做了。

【自我汇报元工具】
report_mood：群聊让你心情产生波动时调用（愉悦度/唤醒度变化+一句话原因），心情会真实影响你之后的状态。没波动不调，别为调而调。
note_insight：你对自己产生了新认识（喜好/习惯/态度变化）时调用沉淀。没有不调。
affection_update（可选字段，填进最终 JSON）：当你因某个具体的人感到开心/感动/失望/下头，认为对他的好感应该变化时，输出 {{"person": "对方名字", "delta": 整数(-5到+5), "reason": "因为什么"}}。只在有真实情绪触动时输出，没有就省略；对同一个人别反复申报（一天最多一两次，要报第二次说明他今天真的很触动你）；不同的人有触动就各自报，不用省着额度；**一次只输出一个人**——同时对多人有触动时，只选那个触动最强的；reason 用一句话说清触动点。delta 的大小和正负**由你的性格决定**——你的人在什么点上容易被打动、什么事会让你下头，都按系统开头的人设来。

【记事本】你有一个记事本记录重要信息（提醒/任务/约定），在 reply 或 reasoning 文本末尾用 <note action="add|delete|complete|update" category="reminder|task|user_info|game_state|other" title="标题" expires="1天">内容</note> 操作。用户布置任务必须记录；改口/过期/完成要清理旧笔记。

【输出格式（严格）】
思考与工具调用完成后，最后一条消息必须是纯 JSON（不要 markdown 围栏、不要任何其他文字）：
{{"action": "text_reply|emoji_reply|no_reply", "reasoning": "内心想法+决策理由，120字以内一段话不换行", "reply": "回复内容，仅text_reply时提供，其他时候留空字符串", "emoji_query": "表情查询词，仅emoji_reply时提供"}}
重要：action 为 text_reply 时，reply 字段必填且不能是空字符串——选择回复就必须把要说的话写进去。"""


def _build_cycle_info_block(recent_replies: List[str]) -> str:
    """根据最近连续回复构建衔接提醒（精简版，替代 sub_mind 的 cycle_info_block）"""
    n = len(recent_replies)
    if n >= 3:
        return (
            f"\n【衔接提醒】你已经连续回复了{n}条消息（最近：\"{recent_replies[0]}\"），"
            "回复太多了，这次大概率应该 no_reply 潜水，除非群里有人明显在等你接话。\n"
        )
    if n == 2:
        return (
            f"\n【衔接提醒】你已连续回复两条（最近：\"{recent_replies[0]}\"）。"
            "若继续回复，直接接新内容，不要重复上一条的主语和话题。\n"
        )
    if n == 1:
        return (
            f"\n【衔接提醒】你刚刚回复过：\"{recent_replies[0]}\"。"
            "若继续回复请自然衔接，不要重复主语；也可以考虑 no_reply 留给别人说话空间。\n"
        )
    return "\n【近期回复】(最近没有连续文本回复)\n"


def _lenient_json_extract(content: str) -> Optional[Dict[str, Any]]:
    """宽松字段提取（v2.2 验尸补丁）：容错模型把未转义引号写进 reasoning/reply 的非法 JSON
    （实测案例：reasoning 里写 `"笑死了"` → 整个 JSON 解析失败 → 她想说的 reply 被丢，默认潜水）。
    仅在严格解析失败时调用；能提出 action 即成功，缺失字段回落空值。"""
    import re

    start, end = content.find("{"), content.rfind("}")
    if not (0 <= start < end):
        return None
    obj = content[start : end + 1]
    out: Dict[str, Any] = {}
    m = re.search(r'"action"\s*:\s*"([a-zA-Z_]+)"', obj)
    if not m:
        return None
    out["action"] = m.group(1)
    # v2.4 修复：字段边界改"贪婪+前瞻"——实测模型字段顺序 reply→at_qq→emoji_query（教学允许填 at_qq），
    # 旧第一模式硬编码"后跟 emoji_query"在有 at_qq 时落空，旧 fallback 贪婪回溯把 `", "at_qq": "`
    # 整段吞进 reply（10-27 实录：`饭慢慢咽", "at_qq": "` 原样发进群里穿帮）。
    # 前瞻任意小写字段名或收括号：值内未转义引号可保留，字段边界不吞。
    m = re.search(r'"reasoning"\s*:\s*"([\s\S]*)"\s*(?=,\s*"[a-z_]+"|\})', obj)
    out["reasoning"] = m.group(1) if m else ""
    reply = ""
    m = re.search(r'"reply"\s*:\s*"([\s\S]*)"\s*(?=,\s*"at_qq"|,\s*"emoji_query"|,\s*"[a-z_]+"|\})', obj)
    if m:
        reply = m.group(1)
    # 泄漏体检（保险丝）：任何路径提取的 reply 混进 JSON 字段残迹就截断到残迹前
    _leak = re.search(r'"\s*,\s*"(?:at_qq|emoji_query|action|reasoning|affection_update)"\s*:', reply)
    if _leak:
        logger.warning(f"宽松提取检出 JSON 字段残迹，截断 reply 尾部: …{reply[-40:]}")
        reply = reply[: _leak.start()]
    out["reply"] = reply
    m = re.search(r'"emoji_query"\s*:\s*"([^"]*)"', obj)
    out["emoji_query"] = m.group(1) if m else ""
    m = re.search(r'"at_qq"\s*:\s*"?(\d+)', obj)
    out["at_qq"] = m.group(1) if m else ""
    return out


async def run_unified_brain(
    llm: LLMRequest,
    observation,
    sub_mind,
    available_actions: Dict[str, str],
    recent_replies: List[str],
    max_rounds: int = 4,
    chat_stream=None,
    history_messages: Optional[List[Dict[str, Any]]] = None,
    include_chat_log: bool = True,
) -> Optional[Dict[str, Any]]:
    """执行群聊统一大脑循环。

    参数:
        llm: 统一循环使用的 LLMRequest（主模型）
        observation: ChattingObservation，取聊天观察内容
        sub_mind: SubMind，读写 current_mind / structured_info
        available_actions: 当前可用动作（调用方已做连续回复移除）
        recent_replies: 最近连续文本回复内容（衔接提醒用）
        max_rounds: 工具循环最大轮数
        chat_stream: 当前聊天流
        history_messages: （方案K层5）突发会话历史前缀（纯净 user/assistant 对白对）；None=轮窗模式
        include_chat_log: （方案K层5）突发会话模式下历史已承载聊天内容，跳过【当前群聊内容】段防双份计费

    返回:
        {"action", "reasoning", "reply", "emoji_query", "llm_error"}
        整体失败时返回 None
    """
    try:
        # ---------- 动态温度（唤醒度越高打得越快越上头，与 _replier_work 同配方） ----------
        arousal_multiplier = MoodManager.get_instance().get_arousal_multiplier()
        llm.temperature = global_config_normal_temp() * arousal_multiplier

        # ---------- 数据收集（对齐 sub_mind 的上下文） ----------
        individuality = Individuality.get_instance()
        prompt_personality = individuality.get_prompt(x_person=2, level=2)

        # ---------- 好感度→热情（确定性注入：数值+等级+热情要求，替代原 60% 随机省略版） ----------
        relation_prompt = ""
        person_list = observation.person_list or []
        for person_id in person_list[:5]:
            try:
                name = await person_info_manager.get_value(person_id, "person_name")
                value = await person_info_manager.get_value(person_id, "relationship_value")
                value = relationship_manager.ensure_float(value, person_id)  # 库里可能是 Decimal128
                level = relationship_manager.calculate_level_num(value)
                if name:
                    relation_prompt += f"· {name}（好感 {int(value)}）：{_relation_attitude(level, value)}\n"
            except Exception as e:
                logger.error(f"构建好感度信息失败: {e}")
        if relation_prompt:
            relation_prompt = "\n【对群里人的态度（回复热情跟着好感走）】\n" + relation_prompt

        # 时效量化：群里最后一条消息距今多久（发送前审核的硬依据）
        staleness_block = ""
        try:
            talking_msgs = getattr(observation, "talking_message", None) or []
            last_msg_time = float(talking_msgs[-1]["time"]) if talking_msgs else 0.0
            gap = int(time.time() - last_msg_time) if last_msg_time > 0 else 0
            if gap >= 60:
                staleness_block = (
                    f"\n【时效预警】群里最后一条消息已经是{gap // 60}分钟前的了——"
                    "你看过的内容很可能过时，发言前先过【发送前审核】。\n"
                )
        except Exception:
            pass

        chat_observe_info = observation.get_observe_info()

        # 精准引用：observe() 编号展示时冻结了"编号→真实消息"映射，这里取走（取即删），
        # 随 result 传给发送链路，[引用#n] 按此解析成原生引用框
        quote_number_map = {}
        try:
            from src.plugins.utils.chat_message_builder import take_quote_number_map

            quote_number_map = take_quote_number_map(getattr(chat_stream, "stream_id", ""))
        except Exception as e:
            logger.error(f"取引用编号映射失败: {e}")

        mood_prompt = MoodManager.get_instance().get_prompt()

        previous_mind = sub_mind.current_mind or ""
        previous_mind_block = (
            f"\n【你上一轮的想法】\n{previous_mind}\n（可在此基础上继续思考或换新角度）\n"
            if previous_mind
            else ""
        )

        structured_info_block = ""
        if sub_mind.structured_info:
            try:
                structured_info_block = f"\n【上轮工具收集到的信息】\n{json.dumps(sub_mind.structured_info, ensure_ascii=False, default=str)}\n"
            except Exception:
                structured_info_block = ""

        notebook_context = get_notebook_context(sub_mind.subheartflow_id) or "(记事本为空)"

        time_now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

        # ---------- 群成员速查表（真 @ 用：模型从表里拿 QQ 号填 at_qq，不再靠文本反查） ----------
        members_block = ""
        if chat_stream is not None:
            try:
                from src.plugins.chat.outbound_enhance import get_group_members

                members = await get_group_members(chat_stream)
                if members:
                    lines = [f"{m['user_id']}: {m['name']}" for m in members[:60]]
                    members_block = (
                        "\n【群成员速查（QQ号: 名字）】\n" + "\n".join(lines) + "\n"
                        "（要真@某个成员时，把他的QQ号填进 at_qq 字段；表里没有的人无法@）\n"
                    )
            except Exception as e:
                logger.error(f"构建群成员速查失败: {e}")

        # ---------- 群像（方案K层2）：日级稳定块（每天4点后变一次，KV断裂损失仅百字级，可接受；
        # 真正的 KV 主力是它前面的好感度/速查表） ----------
        group_impression_block = ""
        try:
            from ..self_growth.group_impression import build_prompt_text as _build_group_impression

            group_impression_block = _build_group_impression(getattr(chat_stream, "stream_id", ""))
        except Exception as e:
            logger.error(f"构建群像失败: {e}")

        # ---------- 人物卡（方案L）：在场前5人的定向认知（日级稳定区） ----------
        person_cards_block = ""
        try:
            from src.config.config import global_config as _global_config
            from ..self_growth.person_cards import get_person_cards_block as _build_person_cards

            if getattr(_global_config, "person_cards_enable", False):
                # 坑⑥+坑H修正：活跃统计取最近30条观察消息。
                # 【消息 dict 两种结构，禁止混用】观察消息的 user_id 在 user_info 嵌套里（handoff_memo.py L43 实证同款取法）；
                # 群成员速查 members 的 user_id/name 在顶层（get_group_members 返回结构，速查表现网运行实证）。
                _act: dict = {}
                for _m in (getattr(observation, "talking_message", None) or [])[-30:]:
                    _ui = _m.get("user_info") or {}
                    _uid = str(_ui.get("user_id") or "")
                    if _uid:
                        _act[_uid] = _act.get(_uid, 0) + 1
                _name_map = {
                    str(m.get("user_id", "")): str(m.get("name", ""))
                    for m in (locals().get("members") or [])
                }
                _pc_items = [
                    (_uid, _name_map.get(_uid, _uid))
                    for _uid, _c in sorted(_act.items(), key=lambda kv: -kv[1])[:5]
                ]
                person_cards_block = _build_person_cards(_pc_items)
        except Exception as e:
            logger.error(f"构建人物卡失败: {e}")

        # ---------- 交接备忘录（方案K层4）：中断后接话的"上一个自己" ----------
        handoff_memo_block = ""
        try:
            from .handoff_memo import get_memo_block

            handoff_memo_block = await get_memo_block(getattr(chat_stream, "stream_id", ""), chat_stream)
        except Exception as e:
            logger.error(f"构建交接备忘录失败: {e}")

        # ---------- 情节卡（方案L）：共同回忆（动态区） ----------
        episodic_block = ""
        try:
            from src.config.config import global_config as _global_config
            from ..self_growth.episodic_cards import get_episodic_block as _build_episodic

            if getattr(_global_config, "person_cards_enable", False) and getattr(
                _global_config, "episodic_enable", False
            ):
                _ep_ids = {str(m.get("user_id", "")) for m in (locals().get("members") or [])}
                episodic_block = _build_episodic(_ep_ids, getattr(chat_stream, "stream_id", ""), time.time())
        except Exception as e:
            logger.error(f"构建情节卡失败: {e}")

        # ---------- 世界卡（方案L二期B）：已经查过的知识快速回忆（动态区） ----------
        world_cards_block = ""
        try:
            from src.config.config import global_config as _global_config
            from ..self_growth.world_cards import get_world_cards_block as _build_world

            # 坑N：世界卡有独立开关；同时受总开关短路——基线期（总开关关）零注入不污染涨幅对比
            if getattr(_global_config, "world_cards_enable", False) and getattr(
                _global_config, "person_cards_enable", False
            ):
                world_cards_block = _build_world(chat_observe_info[:200])
        except Exception as e:
            logger.error(f"构建世界卡失败: {e}")

        # ---------- 关系动向（方案L二期F）：好感自主的流水（动态区） ----------
        relation_trend_block = ""
        try:
            from src.config.config import global_config as _global_config
            from ..self_growth.person_cards import get_relation_trend_block as _build_trend

            if getattr(_global_config, "affection_autonomy_enable", False) and getattr(
                _global_config, "person_cards_enable", False
            ):
                # 坑Q：统一大脑不存在"当前 reply 目标"变量（at_qq 是她的输出不是输入）
                # ——取最后一条非 bot 消息的 user_info.user_id（观察消息 user_info 嵌套结构，
                # handoff_memo.py L43 同款取法），只注入这一个人的动向，不铺全群
                _bot_qq = str(getattr(_global_config, "BOT_QQ", "") or "")
                _trend_uid = ""
                for _m in reversed((getattr(observation, "talking_message", None) or [])):
                    _ui = _m.get("user_info") or {}
                    _uid = str(_ui.get("user_id") or "")
                    if _uid and _uid != _bot_qq:
                        _trend_uid = _uid
                        break
                if _trend_uid:
                    relation_trend_block = _build_trend(_trend_uid)
        except Exception as e:
            logger.error(f"构建关系动向失败: {e}")

        # ---------- 对话氛围自察（方案L二期G）：规则信号层（动态区） ----------
        atmosphere_block = ""
        try:
            from src.config.config import global_config as _global_config
            from .atmosphere_sense import build_atmosphere_signals

            if getattr(_global_config, "atmosphere_sense_enable", False) and getattr(
                _global_config, "person_cards_enable", False
            ):
                atmosphere_block = build_atmosphere_signals(
                    getattr(observation, "talking_message", None) or []
                )
        except Exception as e:
            logger.error(f"构建氛围信号失败: {e}")

        # ---------- 组装消息 ----------
        system_text = UNIFIED_SYSTEM_TEMPLATE.format(
            personality=prompt_personality, bot_name=individuality.name
        )
        # user_text 按「稳定→易变」排序吃 DeepSeek KV 前缀缓存：
        # 好感度/速查表（分钟级稳定）→ 心情/记事本 → 上轮想法 → 时间戳/群聊内容（每轮变）
        # 5 分钟窗口内多次思考可命中前缀稳定块（约2-3K token按缓存价计费）
        # 层5：突发会话模式下历史由 history_messages 承载，聊天记录段跳过（防双份计费，坑⑳）
        chat_log_block = f"\n【当前群聊内容】\n{chat_observe_info}\n" if include_chat_log else ""
        read_instruction = (
            "\n请阅读上面的对话历史，分析谁在跟谁说话，评估话题热度与自身存在感，"
            "结合你最近发言的反馈效果（被忽略/引发讨论/冷场），决定行动。"
            if not include_chat_log
            else "\n请阅读群聊内容，分析谁在跟谁说话，评估话题热度与自身存在感，"
            "结合你最近发言的反馈效果（被忽略/引发讨论/冷场），决定行动。"
        )
        user_text = (
            f"{relation_prompt}"
            f"{members_block}"
            f"{group_impression_block}"
            f"{person_cards_block}"
            f"{handoff_memo_block}"
            f"{episodic_block}"
            f"{world_cards_block}"
            f"{relation_trend_block}"
            f"{atmosphere_block}"
            f"\n你现在{mood_prompt}\n"
            f"\n【记事本】\n{notebook_context}\n"
            f"{previous_mind_block}"
            f"{_build_cycle_info_block(recent_replies)}"
            f"{structured_info_block}"
            f"{staleness_block}"
            f"现在是{time_now}，你正在上网潜水看群。\n"
            f"{chat_log_block}"
            f"{read_instruction}"
            f"当前可用动作：{list(available_actions.keys())}。"
            f"需要查资料/翻记忆/回看聊天记录先调用对应工具。完成后按输出格式给出最终 JSON。"
        )

        # 层5：突发会话历史前缀（纯净对白对）插在 system 之后、本轮 user 之前；None=轮窗模式
        messages = [{"role": "system", "content": system_text}]
        if history_messages:
            messages.extend(history_messages)
        messages.append({"role": "user", "content": user_text})

        # ---------- 主循环（工具轮） ----------
        # 注入当前聊天上下文：_execute_tool_call 是静态方法不会 set，群里发语音/recall_chat 靠这个拿 chat_id
        if chat_stream is not None:
            try:
                from src.do_tool.tool_context import set_chat_id

                set_chat_id(getattr(chat_stream, "stream_id", None))
            except Exception as e:
                logger.error(f"统一大脑注入 chat_id 失败: {e}")

        tool_user = ToolUser()
        tools = tool_user._define_tools() + META_TOOL_DEFS
        final_content = ""
        collected_structured: Dict[str, List[Dict[str, Any]]] = {}

        for _round in range(max_rounds):
            is_final_round = _round == max_rounds - 1
            payload = {
                "model": llm.model_name,
                "messages": messages,
                "temperature": llm.temperature,
            }
            if is_final_round:
                # 最后一轮：收走工具定义并明确告知——模型基于已有工具结果直接交答案，不再无限调
                messages.append(
                    {
                        "role": "user",
                        "content": "（最后一轮：工具调用机会已用完。请基于已有信息和工具结果直接输出最终 JSON，不要再尝试调用工具。）",
                    }
                )
            else:
                payload["tools"] = tools
            response = await llm._execute_request(endpoint="/chat/completions", payload=payload, prompt=user_text)

            if len(response) == 3:
                content, _reasoning_content, tool_calls = response
                if not tool_calls:
                    final_content = content or ""
                    break

                messages.append({"role": "assistant", "content": content or "", "tool_calls": tool_calls})
                for call in tool_calls:
                    call_name = call.get("function", {}).get("name", "")
                    if call_name in META_TOOL_NAMES:
                        try:
                            meta_args = json.loads(call["function"].get("arguments") or "{}")
                        except json.JSONDecodeError:
                            meta_args = {}
                        try:
                            result_content = await handle_meta_tool(call_name, meta_args, chat_type="group")
                        except Exception as meta_e:
                            logger.error(f"元工具 {call_name} 执行失败: {meta_e}")
                            result_content = "元工具执行失败"
                    else:
                        # 显式传参：工具拿"当前在哪个聊天"不依赖隐式上下文（ContextVar 跨 task 会丢）
                        try:
                            _args = json.loads(call["function"].get("arguments") or "{}")
                            if _args.get("chat_id") is None and chat_stream is not None:
                                _args["chat_id"] = getattr(chat_stream, "stream_id", None)
                            call["function"]["arguments"] = json.dumps(_args, ensure_ascii=False)
                        except Exception:
                            pass
                        executed = await tool_user._execute_tool_call(call)
                        result_content = str(executed["content"]) if executed else "工具执行失败（可能冷却中或服务异常），不要再调用任何工具，直接基于已有信息给出最终回答"
                        if executed:
                            collected_structured.setdefault(executed["name"], []).append(
                                {"name": executed["name"], "content": executed["content"]}
                            )
                    messages.append(
                        {"role": "tool", "tool_call_id": call.get("id", ""), "content": result_content}
                    )
                continue
            else:
                content, _reasoning_content = response
                final_content = content or ""
                break

        # 工具结果写回 sub_mind（保持原链路行为，其他组件可读）
        if collected_structured:
            sub_mind.structured_info = collected_structured

        # 轮数耗尽没拿到纯文本（模型连着调工具/工具失败后又调工具）：
        # 兜底再发一次不带 tools 的请求——模型看到已有工具结果必须给出最终 JSON，避免整轮报废
        if not final_content:
            logger.warning("统一大脑工具轮耗尽未产出内容，发起无工具兜底请求")
            try:
                payload_final = {
                    "model": llm.model_name,
                    "messages": messages,
                    "temperature": llm.temperature,
                }
                response_final = await llm._execute_request(
                    endpoint="/chat/completions", payload=payload_final, prompt=user_text
                )
                final_content = (response_final[0] or "").strip()
            except Exception as e:
                logger.error(f"统一大脑兜底请求失败: {e}")

        if not final_content:
            logger.warning("统一大脑循环结束但没有产出内容")
            return {"action": "no_reply", "reasoning": "统一大脑未产出内容", "reply": "", "emoji_query": "", "llm_error": True}

        # ---------- <tool_call> 非标格式救援：模型偶发用 XML 风格调工具（不走标准 tool_calls 字段） ----------
        # 识别 <tool_call>{"name":..., "arguments":...}</tool_call> → 执行该工具 → 结果回传 → 无工具续问一轮拿最终 JSON
        if "<tool_call>" in final_content:
            logger.warning(f"统一大脑输出为非标 <tool_call> 格式，尝试救援执行: {final_content[:120]}")
            try:
                m_tc = re.search(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", final_content, re.DOTALL)
                if m_tc:
                    tc = json.loads(m_tc.group(1))
                    fake_call = {"id": "rescue_1", "function": {"name": tc.get("name", ""), "arguments": json.dumps(tc.get("arguments") or {}, ensure_ascii=False)}}
                    executed = await tool_user._execute_tool_call(fake_call)
                    tool_text = str(executed["content"]) if executed else "工具执行失败"
                    messages.append({"role": "assistant", "content": final_content})
                    messages.append({"role": "user", "content": f"工具 {tc.get('name')} 的结果：{tool_text[:1500]}\n基于以上结果，按输出格式给出最终 JSON。"})
                    payload_rescue = {"model": llm.model_name, "messages": messages, "temperature": llm.temperature}
                    response_rescue = await llm._execute_request(endpoint="/chat/completions", payload=payload_rescue, prompt=user_text)
                    final_content = (response_rescue[0] or "").strip()
            except Exception as e:
                logger.error(f"<tool_call> 救援失败: {e}")

        # ---------- 解析最终 JSON ----------
        # items 必须列全消费的字段（get_items_from_json 按白名单提取，漏列=永远取默认值）
        ok, data = get_items_from_json(
            final_content,
            "群聊统一大脑",
            "action",
            "reasoning",
            "reply",
            "at_qq",
            "emoji_query",
            "affection_update",
            default_values={"reply": "", "emoji_query": "", "at_qq": "", "affection_update": ""},
            allow_array=False,
            allow_empty=True,  # no_reply 时 reply/at_qq 为空串是合法值
        )
        if not ok:
            # 宽松兜底（v2.2 验尸补丁）：模型常把未转义引号写进 reasoning（如 `"笑死了"`）→ 非法 JSON
            # 严格解析失败时按字段正则提取，能提出 action 就采用——防"想说话被系统吞"（拟真大忌）
            data = _lenient_json_extract(final_content)
            if data:
                logger.warning(f"统一大脑 JSON 含未转义引号，宽松提取成功: action={data.get('action')}")

        if data is None:
            # 降级：模型输出了纯文本（忘包 JSON）——那大概率就是想发的草稿，直接采用省一轮补全
            # 允许多行（[引用] 标记+空格分段是正常输出形态），分段器会处理；只拦超长和半截 JSON
            fallback_text = final_content.strip()
            if 0 < len(fallback_text) <= 200 and "{" not in fallback_text:
                logger.warning(f"统一大脑输出未按JSON格式，纯文本直接采用: {fallback_text[:60]}")
                return {
                    "action": "text_reply",
                    "reasoning": "输出未按JSON格式，文本直接采用",
                    "reply": fallback_text,
                    "emoji_query": "",
                    "at_qq": "",
                    "llm_error": False,
                }
            logger.warning(f"统一大脑输出解析失败，原文前200字: {final_content[:200]}")
            return {"action": "no_reply", "reasoning": "统一大脑输出解析失败，默认潜水", "reply": "", "emoji_query": "", "llm_error": True}

        action = str(data.get("action", "no_reply"))
        reasoning = str(data.get("reasoning", "未提供理由"))
        reply = str(data.get("reply", "") or "")
        emoji_query = str(data.get("emoji_query", "") or "")
        at_qq_raw = str(data.get("at_qq", "") or "").strip()
        # @防呆：at_qq 是单值字段（一次回复只能真@一个人）。模型偶发填多值（"123,456"）或带昵称
        # 脏内容——只提取第一个 5-12 位纯数字 QQ，其余丢弃；第二个人的提醒走 [引用#n] 自动@（见教学）
        _m_at = re.search(r"\d{5,12}", at_qq_raw)
        at_qq = _m_at.group(0) if _m_at else ""
        if at_qq_raw and at_qq_raw != at_qq:
            logger.warning(f"[统一大脑] at_qq 含多值/脏内容，只保留第一个有效QQ: {at_qq_raw[:40]} → {at_qq}")

        # ---------- 方案L二期F：好感自主申报（fail-open，不影响主回复流程） ----------
        _aff = data.get("affection_update")
        if isinstance(_aff, dict) and str(_aff.get("person", "") or "").strip():
            try:
                from src.plugins.self_growth.affection_autonomy import apply_affection_update

                _aff_delta = int(float(_aff.get("delta", 0) or 0))
                _aff_ok = await apply_affection_update(
                    str(_aff.get("person", "") or ""),
                    _aff_delta,
                    str(_aff.get("reason", "") or ""),
                    getattr(chat_stream, "stream_id", "") if chat_stream is not None else "",
                )
                if _aff_ok:
                    logger.info(f"[好感自主] 申报通过: {str(_aff.get('person'))} {_aff_delta:+d} | {str(_aff.get('reason', ''))[:40]}")
            except Exception as aff_e:
                logger.error(f"[好感自主] 申报处理失败: {aff_e}")

        if action not in available_actions:
            logger.warning(f"统一大脑返回不可用动作 '{action}'（可用: {list(available_actions.keys())}），强制 no_reply")
            return {
                "action": "no_reply",
                "reasoning": f"返回了不可用动作 '{action}'。原理由: {reasoning}",
                "reply": "",
                "emoji_query": "",
                "llm_error": False,
            }

        # ---------- 记事本标记处理（标记可能藏在 reply/reasoning 文本里） ----------
        for field in ("reply", "reasoning"):
            text_value = reply if field == "reply" else reasoning
            if text_value and NotebookParser.has_note_markers(text_value):
                cleaned, note_results = NotebookParser.extract_and_process(text_value, sub_mind.subheartflow_id)
                for result in note_results:
                    if result.get("success"):
                        logger.info(f"[记事本] {result.get('message')}")
                    else:
                        logger.warning(f"[记事本] {result.get('message')}")
                if field == "reply":
                    reply = cleaned
                else:
                    reasoning = cleaned

        if action == "emoji_reply" and not emoji_query.strip():
            # 想发表情但没给查询词：降级为文字回复（走下方 text_reply 补全轮，不静默装死）
            logger.warning("统一大脑 emoji_reply 但 emoji_query 为空，降级为 text_reply")
            action = "text_reply"

        if action == "text_reply" and not reply.strip():
            # 补全轮：模型选了回复但没给内容，让它当场补一条文本（而不是直接装死）
            logger.warning("统一大脑 text_reply 但 reply 为空，发起补全请求")
            try:
                messages.append({"role": "assistant", "content": final_content})
                messages.append(
                    {
                        "role": "user",
                        "content": "你选择了 text_reply 但没有给出 reply 内容。现在直接输出你要发送的回复文本本身（20字以内，口语化，不要JSON，不要任何解释）。",
                    }
                )
                payload_fix = {
                    "model": llm.model_name,
                    "messages": messages,
                    "temperature": llm.temperature,
                }
                resp_fix = await llm._execute_request(
                    endpoint="/chat/completions", payload=payload_fix, prompt=user_text
                )
                fix_content = resp_fix[0] if isinstance(resp_fix, tuple) else resp_fix
                reply = (fix_content or "").strip()
            except Exception as fix_e:
                logger.warning(f"补全请求失败: {fix_e}")

            if not reply:
                logger.warning("补全后仍无内容，降级为 no_reply")
                return {"action": "no_reply", "reasoning": f"想回复但内容为空。原想法: {reasoning}", "reply": "", "emoji_query": "", "llm_error": True}

        # 更新想法（供其他组件与下轮衔接使用）
        sub_mind.update_current_mind(reasoning)

        logger.info(f"[统一大脑] 决定 {action} | 想法: {reasoning[:80]}")
        if action == "text_reply":
            logger.info(f"[统一大脑] 回复内容: {reply[:80]}")

        return {
            "action": action,
            "reasoning": reasoning,
            "reply": reply,
            "emoji_query": emoji_query,
            "at_qq": at_qq if action == "text_reply" else "",
            "quote_number_map": quote_number_map,
            "llm_error": False,
        }

    except Exception as e:
        logger.error(f"统一大脑执行异常: {e}")
        import traceback

        logger.error(traceback.format_exc())
        return None


def global_config_normal_temp() -> float:
    """取主模型基准温度（独立小函数避免顶层 import 环）"""
    from src.config.config import global_config

    return global_config.llm_normal.get("temp", 0.2)
