"""方案L二期G：对话氛围自察（规则信号层，零 LLM 成本，每轮思考计算）。

设计立场（老大原话）：氛围感知的终点**不是让她贴合氛围，是给她选择权**——悲伤的气氛里，
共情是温柔，跳出来讲个笑话破局是勇气，选哪个是她的事。这就是人性的光辉。

只陈述现象，不做判断——判断由模型自己做（决策条款在 UNIFIED_SYSTEM_TEMPLATE【氛围与回应的先后】）。
信号源：观察消息（user_info 嵌套结构，handoff_memo.py L43 同款取法）。
"""
import time
from typing import List

from src.common.logger_manager import get_logger

logger = get_logger("atmosphere_sense")

# 悲伤词两档（坑S）：强词照报，弱词有玩梗伴生词则跳过计数
_STRONG_SAD_WORDS = ("哭", "难过", "去世")
_WEAK_SAD_WORDS = ("emo", "寄了", "累死了")
_PLAYFUL_WORDS = ("哈哈", "笑", "草")


def _msg_text(m: dict) -> str:
    """观察消息的文本（拿不到就空串，各信号自行跳过）。"""
    try:
        return str(m.get("processed_plain_text", "") or "").strip()
    except Exception:
        return ""


def build_atmosphere_signals(talking_message: list) -> str:
    """从观察消息提取规则信号，返回注入文本（无信号返回空串）。只陈述现象，不做判断。

    · 节奏：最近5分钟 N 条 / 此前30分钟 M 条
    · 冷场：最后一条消息距今 X 分钟（≥3分钟才报）
    · 情绪浓度：问号连发 / 感叹号密度 / "哈哈哈"连发
    · 疲态：最近3条平均长度比之前短一半以上
    · 复读：同一句话被复述 ≥3 次
    · 悲伤线索：低落词密度（弱词档含玩梗伴生词则跳过计数）"""
    try:
        msgs = [m for m in (talking_message or []) if isinstance(m, dict)]
        if not msgs:
            return ""
        now = time.time()
        signals: List[str] = []

        # 节奏
        recent_5m = [m for m in msgs if float(m.get("time", 0) or 0) >= now - 300]
        prior_30m = [m for m in msgs if now - 1800 <= float(m.get("time", 0) or 0) < now - 300]
        if prior_30m and len(recent_5m) <= len(prior_30m) // 3:
            signals.append(f"· 节奏：最近5分钟 {len(recent_5m)} 条 / 此前30分钟 {len(prior_30m)} 条（明显变慢，可能在冷场）")
        elif len(recent_5m) >= 8 and len(recent_5m) > max(1, len(prior_30m)) * 2:
            signals.append(f"· 节奏：最近5分钟 {len(recent_5m)} 条 / 此前30分钟 {len(prior_30m)} 条（骤升，可能玩嗨了）")

        # 冷场
        last_ts = float(msgs[-1].get("time", 0) or 0)
        if last_ts:
            gap_min = int((now - last_ts) / 60)
            if gap_min >= 3:
                signals.append(f"· 冷场：最后一条消息距今 {gap_min} 分钟")

        texts = [_msg_text(m) for m in msgs]
        texts_nonempty = [t for t in texts if t]
        window = texts_nonempty[-10:]

        # 情绪浓度
        q_burst = sum(1 for t in window if ("？？" in t or "??" in t))
        if q_burst >= 2:
            signals.append(f"· 情绪浓度：问号连发 {q_burst} 次（像质问或追问）")
        excl = sum(1 for t in window if "！" in t or "!" in t)
        if window and excl / len(window) > 0.5 and excl >= 3:
            signals.append(f"· 情绪浓度：感叹号密度高（{excl}/{len(window)}，激烈）")
        haha = sum(1 for t in window if "哈哈" in t)
        if haha >= 3:
            signals.append(f"· 情绪浓度：\"哈哈哈\"连发 {haha} 条（玩嗨）")

        # 疲态：最近3条平均长度比之前短一半以上（之前至少3条可比）
        if len(texts_nonempty) >= 6:
            recent3 = [len(t) for t in texts_nonempty[-3:]]
            earlier = [len(t) for t in texts_nonempty[-13:-3]]
            avg_r = sum(recent3) / 3
            avg_e = sum(earlier) / len(earlier) if earlier else 0
            if avg_e >= 6 and avg_r < avg_e / 2:
                signals.append(f"· 疲态：最近3条平均长度({avg_r:.0f}字)比之前({avg_e:.0f}字)短一半以上（敷衍或累了）")

        # 复读
        counter: dict = {}
        for t in texts_nonempty[-15:]:
            key = t[:40]
            if key:
                counter[key] = counter.get(key, 0) + 1
        top_text, top_n = max(counter.items(), key=lambda kv: kv[1]) if counter else ("", 0)
        if top_n >= 3:
            signals.append(f"· 复读：\"{top_text[:20]}\"被复述 {top_n} 次（玩梗中）")

        # 悲伤线索（坑S：只报密度不判语境，判断权在模型；弱词档含玩梗伴生词跳过）
        strong_hit = 0
        weak_hit = 0
        for t in window:
            tl = t.lower()
            if any(w in t for w in _STRONG_SAD_WORDS):
                strong_hit += 1
            elif any(w in tl for w in _WEAK_SAD_WORDS) and not any(w in t for w in _PLAYFUL_WORDS):
                weak_hit += 1
        if strong_hit:
            signals.append(f"· 悲伤线索：出现低落词（强）{strong_hit} 条")
        if weak_hit:
            signals.append(f"· 悲伤线索：出现低落词（弱）{weak_hit} 条")

        if not signals:
            return ""
        return "\n【对话氛围自察】（供你感受，不是指令）\n" + "\n".join(signals) + "\n"
    except Exception as e:
        logger.error(f"构建氛围信号失败: {e}")
        return ""
