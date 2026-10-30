"""Token 预算闸门（S2，M3.2，吸收自 SurfCore token-budget / token-guard / token-ledger）

解决什么：Moui 的 LLM 调用面很广（主链路+审核+成长层+主动行为），失控烧钱没有闸。
SurfCore 答案：每日预算 + 三档熔断 + 80% 提醒落盘 + 坏配置回落继续闸。

记账走旁路（1审红坑①）：utils_model._handle_response 拿到原始 response 后调 record()——
不动 _execute_request 返回签名，调用方无感。embedding 也经此路（Y-A5 已核：
get_embedding → _execute_request，endpoint=/embeddings，记账自动覆盖）。
闸门挂 _execute_request 开头（预算是时间函数，Y6），熔断 raise TokenBudgetExceeded
由调用方既有 try/except 兜住（三审 Y-A6）。

三档：
  used < 0.8×预算        → 放行一切
  0.8 ~ 1.0              → 白名单外熔断（embedding 类除外：记账不熔断，记忆检索是回复链路一环且便宜）
  ≥ 1.0                  → 仅 CHAT_WHITELIST 放行（她还能回话，降级模式）

故障策略两种（1审红坑②）：
  ① 配置损坏/缺键 → 回落 DEFAULT_DAILY_CNY=8.0 继续闸
    （SurfCore 踩坑⑧：回落"不限"=闸门静默消失；daily_cny 显式填 0 才是"不限"）
  ② 账本读取异常 → fail-open 放行本次（闸门自身故障不能掐死主链路）

账本：data/token_usage.jsonl 每调用一行
  {"t": 1769616000, "type": "unified_brain", "model": "...", "in": 8420, "out": 380, "cny": 0.0272}
性能：内存累加器按日累计，启动（首次 record/check）扫当日 jsonl 建基线，之后只 append 行
+累加内存——每次调用零读放大（✎v2）。同步 IO 由调用方 asyncio.to_thread 包装（2审 Y4）。

铁律：闸门/记账异常绝不影响 LLM 主链路；常量化放头部；getattr 防御读配置。
"""
import json
import os
import time
from typing import Dict, Tuple

from src.common.logger_manager import get_logger

logger = get_logger("token_budget")

LEDGER_FILE = os.path.join("data", "token_usage.jsonl")
ALERT_FILE = os.path.join("data", "token_alert.json")

DEFAULT_DAILY_CNY = 8.0  # 默认每日预算（元），配置损坏/缺键时回落继续闸（对齐 SurfCore 默认）
DEFAULT_ALERT_RATIO = 0.8  # 80% 提醒线（一天一次，落盘即记账：SurfCore 踩坑⑨ 不落盘=重启重弹）

# 聊天主链路白名单：100% 熔断也放行（漏一个 = 她哑或输入残缺）。
# 清单为 grep 全仓 request_type= 代核结果（2审R3：照抄非真实字符串=漏 group_unified_brain=全群死寂），
# 施工时如新增槽位需同步维护。heart_flow（Mind 主心流思考）由老大裁决进白名单。
CHAT_WHITELIST = {
    "group_unified_brain",  # 统一大脑主调用（heartFC_chat.py）
    "response_heartflow",  # 心流回复（heartFC_chat.py）
    "response_reasoning",  # normal_chat_generator 生成
    "action_planning",  # 动作规划（heartFC_chat / PFC action_planner）
    "relation",  # 关系判定（回复链路一环）
    "tool_use",  # 工具调用 LLM（停了工具全哑）
    "chat_observation",  # observation.py 观察压缩（统一大脑的输入源）
    "sub_heart_flow",  # sub_mind.py 子心流
    "subheartflow_state_eval",  # 心流状态评估
    "burst_compact",  # session_pool 爆发会话压缩
    "handoff_memo",  # 交接备忘
    "reply_generation",  # PFC reply_generator
    "reply_check",  # PFC reply_checker（审核停→fail-open 放行，但让它跑着更稳）
    "conversation_goal",  # PFC 目标
    "heart_flow",  # Mind 主心流思考（heartflow.py，老大裁决 2026-10-29）
}

# embedding 类：记账但不熔断（记忆检索是回复链路一部分，价格 0.7 便宜）
EMBEDDING_TYPES = {"info_retrieval", "prompt_build", "embedding"}

# 白名单制：未在 CHAT_WHITELIST 的 type 一律按受控处理（新槽位默认受控）


class TokenBudgetExceeded(Exception):
    """S2 熔断异常：由调用方既有 try/except 兜住（fail-open 家族惯例），不新增调用方改动"""

    def __init__(self, request_type: str = ""):
        self.request_type = request_type
        super().__init__(f"token 预算熔断: {request_type}")


# ===== 内存累加器（进程内按日累计；跨天旧 day 键自然废弃，新 day 从 0 起） =====
_accum: Dict[str, Dict[str, float]] = {}  # day -> {"cny": x, "in": n, "out": n}
_ledger_ready = False
_last_record_sig = {"key": "", "at": 0.0}  # 流式重试去重信号（SurfCore 踩坑⑥⑦：重复计费）


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _acc_add(day: str, cny: float, t_in: int, t_out: int) -> None:
    bucket = _accum.setdefault(day, {"cny": 0.0, "in": 0, "out": 0})
    bucket["cny"] += float(cny or 0)
    bucket["in"] += int(t_in or 0)
    bucket["out"] += int(t_out or 0)


def parse_budget(raw_daily, raw_ratio) -> Tuple[float, float]:
    """配置解析（纯函数，测试直抄）。缺键/非数字 → 回落默认继续闸；
    daily_cny 显式 ≤0 = 不限（闸门放行、记账继续）；ratio 出界 → 回落默认。"""
    try:
        daily = float(raw_daily)
    except (TypeError, ValueError):
        daily = DEFAULT_DAILY_CNY
    try:
        ratio = float(raw_ratio)
        if not (0.0 < ratio < 1.0):
            ratio = DEFAULT_ALERT_RATIO
    except (TypeError, ValueError):
        ratio = DEFAULT_ALERT_RATIO
    return daily, ratio


def _get_budget() -> Tuple[float, float]:
    try:
        from src.config.config import global_config

        return parse_budget(
            getattr(global_config, "token_budget_daily_cny", None),
            getattr(global_config, "token_budget_alert_ratio", None),
        )
    except Exception:
        return DEFAULT_DAILY_CNY, DEFAULT_ALERT_RATIO


def _ensure_ledger() -> None:
    """启动（首次调用）扫当日 jsonl 建基线，之后只 append+累加。读失败 → 空基线继续。"""
    global _ledger_ready
    if _ledger_ready:
        return
    _ledger_ready = True
    try:
        today = _today()
        if os.path.exists(LEDGER_FILE):
            with open(LEDGER_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    try:
                        if time.strftime("%Y-%m-%d", time.localtime(row.get("t", 0))) == today:
                            _acc_add(today, row.get("cny", 0.0), row.get("in", 0), row.get("out", 0))
                    except Exception:
                        continue
    except Exception as e:
        logger.error(f"[token预算] 账本基线建立失败（按空基线继续）: {e}")


def record(request_type: str, model: str, usage: dict, pri_in: float = 0.0, pri_out: float = 0.0) -> None:
    """记账旁路：utils_model._handle_response 拿到 usage 后调（调用方 to_thread 包装，Y4）。
    折算：cny = in/1e6*pri_in + out/1e6*pri_out（pri 单位 = 元/百万 token，bot_config 各槽位标价）。
    去重：同 (type,model,in,out) 且间隔 <1s 视为流式重试的同一响应，不重复计（SurfCore 踩坑⑦）。"""
    try:
        _ensure_ledger()
        today = _today()
        t_in = int((usage or {}).get("prompt_tokens", 0) or 0)
        t_out = int((usage or {}).get("completion_tokens", 0) or 0)
        cny = t_in / 1e6 * float(pri_in or 0) + t_out / 1e6 * float(pri_out or 0)
        sig = f"{request_type}|{model}|{t_in}|{t_out}"
        now = time.time()
        if sig == _last_record_sig["key"] and now - _last_record_sig["at"] < 1.0:
            return
        _last_record_sig["key"] = sig
        _last_record_sig["at"] = now
        _acc_add(today, cny, t_in, t_out)
        os.makedirs(os.path.dirname(LEDGER_FILE), exist_ok=True)
        with open(LEDGER_FILE, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {"t": int(now), "type": request_type, "model": model, "in": t_in, "out": t_out, "cny": round(cny, 6)},
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception as e:
        logger.error(f"[token预算] 记账失败（不影响主链路）: {e}")


def _alert_once(used: float, daily: float, ratio: float) -> None:
    """80% 提醒：跨线的首次调用落盘 data/token_alert.json（记 day），同日不重弹。只提醒不拦截。"""
    try:
        today = _today()
        data = {}
        if os.path.exists(ALERT_FILE):
            with open(ALERT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        if isinstance(data, dict) and data.get("day") == today:
            return
        os.makedirs(os.path.dirname(ALERT_FILE), exist_ok=True)
        with open(ALERT_FILE, "w", encoding="utf-8") as f:
            json.dump({"day": today, "used": round(used, 2), "budget": daily}, f, ensure_ascii=False)
        logger.warning(f"[token预算] 已用 {used:.2f} 元 / 预算 {daily:.2f} 元（{ratio:.0%}），主动行为开始熔断")
    except Exception as e:
        logger.error(f"[token预算] 提醒落盘失败: {e}")


def gate_decision(rt: str, used: float, daily: float, ratio: float) -> Tuple[bool, str]:
    """三档判定（纯函数，测试直抄）。
    白名单/embedding 永远放行；其余：<0.8 放行 / 0.8~1.0 熔断 / ≥1.0 熔断。"""
    rt = str(rt or "default")
    if rt in CHAT_WHITELIST or rt in EMBEDDING_TYPES:
        return True, "whitelist"
    if daily <= 0:
        return True, "unlimited"  # 显式 0=不限（闸门放行，记账继续）
    if used < daily * ratio:
        return True, f"ok({used:.2f}/{daily:.2f})"
    return False, f"budget({used:.2f}/{daily:.2f})"


def check_gate(request_type: str) -> Tuple[bool, str]:
    """闸门（同步，调用方在 _execute_request 开头 to_thread 调用）。
    故障策略：账本/配置异常 fail-open 放行（闸门自身故障不能掐死主链路）。"""
    try:
        daily, ratio = _get_budget()
        _ensure_ledger()
        used = _accum.get(_today(), {}).get("cny", 0.0)
        ok, why = gate_decision(request_type, used, daily, ratio)
        if not ok:
            _alert_once(used, daily, ratio)
            logger.warning(f"[token预算] 80%，主动行为熔断: {request_type} ({why})")
        return ok, why
    except Exception as e:
        logger.error(f"[token预算] check_gate 异常（放行本次）: {e}")
        return True, f"gate_fault:{e}"


def get_today_usage() -> Dict[str, float]:
    """当日累计（验收对账用：jsonl 记账 vs llm_statistics ±5%）"""
    try:
        _ensure_ledger()
        b = _accum.get(_today(), {})
        return {"cny": round(b.get("cny", 0.0), 4), "in": int(b.get("in", 0)), "out": int(b.get("out", 0))}
    except Exception:
        return {"cny": 0.0, "in": 0, "out": 0}
