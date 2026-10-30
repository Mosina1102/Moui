"""方案K监控看板：挂在消息服务同一 FastAPI 上（浏览器 http://HOST:PORT/dashboard）。

只读接口：/api/k/overview|scores|impressions|entries|journal|memos|sessions|mood|reflections|reflection|logs|log_modules
M3.2 新增只读接口：/api/k/purpose（动机池状态）/api/k/budget（今日预算闸门）/api/k/world_cards（世界卡）
操作接口（需 dashboard_token，token 留空配置=禁用全部操作）：
    /api/k/action/reflect  /api/k/action/review  /api/k/action/cleanup
    /api/k/entries/delete  /api/k/impressions/set
所有数据源都是现成文件/单例，零新依赖；全程 try/except（铁律3 fail-open）。
"""
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Query
from fastapi.responses import HTMLResponse, JSONResponse

from src.common.logger_manager import get_logger
from src.config.config import global_config

logger = get_logger("dashboard")

_DASH_DIR = Path(__file__).parent
_LOG_ROOT = Path("logs")
_STORE_DIR = Path("data") / "self_memory"
_MODULE_NAME_RE = re.compile(r"[A-Za-z0-9_]+")

# 集权·配置热改白名单：中文标签 + 说明 + kind + 全局属性名(None=重启生效) + 取值范围
# hot=True 的字段运行时 getattr 实时读取（方案K模块与统一大脑），保存即生效
_EDITABLE: Dict[str, Dict[str, Any]] = {
    "bot": {
        "owner_qq": {"kind": "str", "attr": "BOT_OWNER_QQ", "rng": None, "label": "主人的QQ号", "desc": "动机池/私聊素材用（S1）；留空=从私聊流自动推断（清空需直接改配置文件）"},
    },
    "token_budget": {
        "daily_cny": {"kind": "float", "attr": "token_budget_daily_cny", "rng": (0, 1000), "label": "每日预算（元）", "desc": "全天token总预算，0=不限；80%后主动行为熔断，100%仅聊天主链路放行（热生效）"},
        "alert_ratio": {"kind": "float", "attr": "token_budget_alert_ratio", "rng": (0.05, 0.99), "label": "熔断比例", "desc": "超过这个比例开始熔断主动行为（默认0.8，热生效）"},
    },
    "plastic_memory": {
        "enable": {"kind": "bool", "attr": "plastic_memory_enable", "rng": None, "label": "方案K总开关", "desc": "关掉后打分/群像/反思/交接全部静默"},
        "group_review_enabled": {"kind": "bool", "attr": "plastic_group_review_enabled", "rng": None, "label": "群像每日审查", "desc": "每天自动更新她对各群的印象"},
        "group_review_hour": {"kind": "int", "attr": "plastic_group_review_hour", "rng": (0, 23), "label": "群像审查时刻", "desc": "几点开始审查（各群错峰几分钟），建议避开聊天高峰"},
        "reflection_enabled": {"kind": "bool", "attr": "plastic_reflection_enabled", "rng": None, "label": "每日反思", "desc": "生成反思报告并沉淀经验条目"},
        "reflection_hour": {"kind": "int", "attr": "plastic_reflection_hour", "rng": (0, 23), "label": "反思时刻", "desc": "每天几点复盘，建议凌晨"},
        "score_retention_days": {"kind": "int", "attr": "plastic_score_retention_days", "rng": (1, 365), "label": "打分流保留天数", "desc": "越久趋势越准，占盘越多"},
        "handoff_min_gap_min": {"kind": "int", "attr": "plastic_handoff_min_gap_min", "rng": (1, 120), "label": "交接最短间隔", "desc": "她中断多少分钟后才需要交接备忘（分钟）"},
        "handoff_max_gap_min": {"kind": "int", "attr": "plastic_handoff_max_gap_min", "rng": (1, 240), "label": "交接最长间隔", "desc": "超过视为话题已死，不再交接（分钟）"},
        "dashboard_token": {"kind": "str", "attr": "plastic_dashboard_token", "rng": None, "label": "看板操作令牌", "desc": "填入看板右上角解锁操作按钮；留空=只读"},
    },
    "person_cards": {
        "enable": {"kind": "bool", "attr": "person_cards_enable", "rng": None, "label": "方案L人物卡", "desc": "人物卡&情节卡归档+注入总开关（首次开启前建议先抓基线日志）"},
        "archive_hour": {"kind": "int", "attr": "person_archive_hour", "rng": (0, 23), "label": "归档时刻", "desc": "几点开始归纳昨天（各群错峰几分钟），与群像审查错开"},
        "episodic_enable": {"kind": "bool", "attr": "episodic_enable", "rng": None, "label": "情节卡", "desc": "共同回忆卡单独开关"},
        "episodic_inject_num": {"kind": "int", "attr": "episodic_inject_num", "rng": (0, 5), "label": "回忆注入条数", "desc": "每轮思考注入的情节卡上限"},
    },
    "experimental": {
        "group_unified_brain": {"kind": "bool", "attr": "enable_group_unified_brain", "rng": None, "label": "统一大脑", "desc": "思考+决策+生成一次循环完成；关掉回退老三段式"},
        "burst_session": {"kind": "bool", "attr": "enable_burst_session", "rng": None, "label": "突发会话实验", "desc": "热聊时用原生上下文（长短模式2.0），只对白名单群生效"},
        "burst_idle_min": {"kind": "int", "attr": "burst_idle_min", "rng": (1, 120), "label": "突发冷场拆除", "desc": "热聊结束多少分钟后拆会话（分钟）"},
        "burst_max_tokens": {"kind": "int", "attr": "burst_max_tokens", "rng": (2000, 60000), "label": "突发的历史上限", "desc": "会话历史超过这个 token 数就压缩拆除"},
    },
    "chat": {
        "base_normal_chat_num": {"kind": "int", "attr": None, "rng": (0, 20), "label": "普通聊天群数上限", "desc": "同时最多几个群处于普通聊天（重启生效）"},
        "base_focused_chat_num": {"kind": "int", "attr": None, "rng": (0, 20), "label": "专注聊天群数上限", "desc": "同时最多几个群进入专注（重启生效）"},
        "observation_context_size": {"kind": "int", "attr": None, "rng": (5, 100), "label": "观察上下文条数", "desc": "她每轮能看到的聊天记录条数（重启生效）"},
        "message_buffer": {"kind": "bool", "attr": None, "rng": None, "label": "消息缓冲器", "desc": "等对方把话说完再回复（重启生效）"},
    },
    "mood": {
        "mood_decay_rate": {"kind": "float", "attr": None, "rng": (0.0, 1.0), "label": "情绪衰减率", "desc": "越低情绪消退越慢、越上头（重启生效）"},
        "mood_intensity_factor": {"kind": "float", "attr": None, "rng": (0.1, 5.0), "label": "情绪强度因子", "desc": "越大情绪波动越猛（重启生效）"},
    },
    "memory": {
        "build_memory_interval": {"kind": "int", "attr": None, "rng": (600, 86400), "label": "记忆构建间隔", "desc": "多久学一次新记忆（秒，重启生效）"},
        "forget_memory_interval": {"kind": "int", "attr": None, "rng": (600, 86400), "label": "记忆遗忘间隔", "desc": "多久执行一轮遗忘（秒，重启生效）"},
        "memory_forget_time": {"kind": "int", "attr": None, "rng": (1, 720), "label": "记忆遗忘时限", "desc": "多久之前的记忆会被淡忘（小时，重启生效）"},
    },
}


def _toml_set_scalar(text: str, section: str, key: str, literal: str) -> str:
    """文本手术：只替换 [section] 块内 key 行的值，保留行尾注释与其余内容（防 tomli_w 重写炸注释）。"""
    lines = text.splitlines(keepends=True)
    # 段头容忍行内注释（bot_config.toml 大量 "[chat] #注释" 样式——实测不带这条 6 段里 5 段热改全失败）
    sec_re = re.compile(rf"^\s*\[{re.escape(section)}\](\s*#.*)?$")
    # 值/注释分组：值段 lazy + 注释必须"空格+#"开头——旧写法 (#*.*)$ 会把 0.8 拆成 0 + ".8"（.8 并进注释组），
    # 替换后变 "9.5  .8" 双值，tomli 直接语法错（实测翻车）
    key_re = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*)(.*?)(\s+#.*)?$")
    out: List[str] = []
    in_sec = False
    found = False
    for ln in lines:
        stripped = ln.rstrip("\n")
        if sec_re.match(stripped):
            in_sec = True
            out.append(ln)
            continue
        if in_sec and re.match(r"^\s*\[", stripped):
            in_sec = False
            out.append(ln)
            continue
        if in_sec:
            m = key_re.match(stripped)
            if m:
                prefix, comment = m.group(1), (m.group(3) or "")
                out.append(f"{prefix}{literal}{comment}\n")
                found = True
                continue
        out.append(ln)
    if not found:
        raise ValueError(f"未在 [{section}] 找到键 {key}")
    return "".join(out)


def _token_ok(token: str) -> bool:
    expect = str(getattr(global_config, "plastic_dashboard_token", "") or "")
    return bool(expect) and str(token or "") == expect


def _deny() -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": "操作被拒绝：dashboard_token 未配置或不匹配（config [plastic_memory] dashboard_token）"},
        status_code=403,
    )


def _read_json(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        logger.error(f"看板读文件失败 {path}: {e}")
        return default


def _tail_file(path: Path, lines: int) -> List[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()[-max(1, min(lines, 500)):]
    except FileNotFoundError:
        return []
    except Exception as e:
        logger.error(f"看板读尾部失败 {path}: {e}")
        return []


def _scores_payload(days: int) -> Dict[str, Any]:
    from . import score_events

    events = score_events.get_recent(days=days)
    total = len(events)
    passed = sum(1 for e in events if e.get("passed"))
    avg = round(sum(float(e.get("score", 5) or 5) for e in events) / total, 2) if total else 0.0

    by_group: Dict[str, Dict[str, Any]] = {}
    for e in events:
        g = e.get("group_name") or e.get("stream_id") or "?"
        slot = by_group.setdefault(g, {"total": 0, "passed": 0, "score_sum": 0.0})
        slot["total"] += 1
        slot["passed"] += 1 if e.get("passed") else 0
        slot["score_sum"] += float(e.get("score", 5) or 5)
    groups = [
        {
            "group": g,
            "total": s["total"],
            "passed": s["passed"],
            "avg": round(s["score_sum"] / s["total"], 2) if s["total"] else 0,
        }
        for g, s in sorted(by_group.items(), key=lambda kv: -kv[1]["total"])
    ]
    worst = sorted(events, key=lambda e: float(e.get("score", 5) or 5))[:12]
    return {
        "days": days,
        "total": total,
        "passed": passed,
        "pass_rate": round(passed / total * 100, 1) if total else 0.0,
        "avg": avg,
        "groups": groups,
        "events": events[-60:],
        "worst": worst,
    }


def register_dashboard(app) -> None:
    """把看板路由挂到消息服务的 FastAPI 实例上（main.py 初始化时调用）。"""

    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard_page():
        try:
            return (_DASH_DIR / "dashboard.html").read_text(encoding="utf-8")
        except Exception as e:
            return HTMLResponse(f"<h1>看板页面加载失败: {e}</h1>", status_code=500)

    @app.get("/api/k/overview", include_in_schema=False)
    async def api_overview():
        try:
            from ..moods.moods import MoodManager
            from ..heartFC_chat.handoff_memo import snapshot as memo_snapshot
            from ..heartFC_chat.session_pool import session_pool
            from . import group_impression, score_events, self_memory

            mood = MoodManager.get_instance()
            cur = getattr(mood, "current_mood", None)
            mood_data = {
                "valence": round(float(getattr(cur, "valence", 0) or 0), 3),
                "arousal": round(float(getattr(cur, "arousal", 0) or 0), 3),
                "reason": str(getattr(mood, "last_reason", "") or ""),
            }

            entries = self_memory.load_entries()
            exp = sum(1 for e in entries if e.get("category") == "experience")

            events = score_events.get_recent(days=1)
            passed = sum(1 for e in events if e.get("passed"))
            avg = round(sum(float(e.get("score", 5) or 5) for e in events) / len(events), 2) if events else 0.0

            memos = memo_snapshot()
            sessions = session_pool.snapshot()

            # 阈值告警（P1）：拟真质量警戒线
            alerts: List[str] = []
            if events:
                pr = round(passed / len(events) * 100, 1)
                if avg < 5.5:
                    alerts.append(f"今日均分 {avg} 低于警戒线 5.5")
                if pr < 70:
                    alerts.append(f"今日通过率 {pr}% 低于警戒线 70%")

            # M3.2 增补：动机池/预算/备份摘要（各自独立 fail-open，缺了不拖垮总览）
            purpose_summary: Dict[str, Any] = {}
            try:
                from .purpose_pool import PURPOSE_DAILY_MAX

                pp = _read_json(_STORE_DIR / "purpose_pool.json", {})
                if pp.get("day") != time.strftime("%Y-%m-%d"):  # 跨天未重置=今天还没跑过，展示归零视图
                    pp = {}
                alive = [
                    p for p in (pp.get("pending") or []) if float(p.get("expires_at", 0) or 0) > time.time()
                ]
                purpose_summary = {
                    "count_today": int(pp.get("count_today", 0) or 0),
                    "daily_max": PURPOSE_DAILY_MAX,
                    "pending_n": len(alive),
                }
            except Exception as e:
                logger.debug(f"overview purpose 摘要跳过: {e}")

            budget_summary: Dict[str, Any] = {}
            try:
                from ...common.token_budget import get_today_usage, parse_budget

                daily, ratio = parse_budget(
                    getattr(global_config, "token_budget_daily_cny", None),
                    getattr(global_config, "token_budget_alert_ratio", None),
                )
                used = float(get_today_usage().get("cny", 0.0) or 0)
                if daily <= 0:
                    tier = "unlimited"
                elif used >= daily:
                    tier = "capped"
                elif used >= daily * ratio:
                    tier = "warning"
                else:
                    tier = "ok"
                budget_summary = {"used": round(used, 2), "daily": daily, "ratio": ratio, "tier": tier}
                if tier == "warning":
                    alerts.append(
                        f"今日 token 已用 ¥{used} / ¥{daily}（≥{int(ratio * 100)}%），主动行为开始熔断"
                    )
                elif tier == "capped":
                    alerts.append(f"今日 token 预算已用尽（¥{used} / ¥{daily}），仅聊天主链路放行")
            except Exception as e:
                logger.debug(f"overview budget 摘要跳过: {e}")

            backups_summary: Dict[str, Any] = {}
            try:
                bk_dir = Path("data") / "backups"
                if bk_dir.is_dir():
                    items = []
                    for p in bk_dir.iterdir():
                        try:
                            items.append((p.stat().st_mtime, p.name))
                        except OSError:
                            continue
                    items.sort(reverse=True)
                    if items:
                        backups_summary = {
                            "count": len(items),
                            "latest_name": items[0][1],
                            "latest_time": items[0][0],
                        }
            except Exception as e:
                logger.debug(f"overview backups 摘要跳过: {e}")

            return {
                "ok": True,
                "ts": time.time(),
                "mood": mood_data,
                "entries": {"total": len(entries), "experience": exp},
                "impressions": len(group_impression.get_all()),
                "scores_today": {"total": len(events), "passed": passed, "avg": avg},
                "memos": {sid: m.get("status", "?") for sid, m in memos.items()},
                "sessions": len(sessions),
                "burst_enabled": bool(getattr(global_config, "enable_burst_session", False)),
                "purpose": purpose_summary,
                "budget": budget_summary,
                "backups": backups_summary,
                "alerts": alerts,
            }
        except Exception as e:
            logger.error(f"overview 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/scores", include_in_schema=False)
    async def api_scores(days: int = 1):
        try:
            return {"ok": True, **_scores_payload(max(1, min(days, 7)))}
        except Exception as e:
            logger.error(f"scores 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/impressions", include_in_schema=False)
    async def api_impressions():
        try:
            from . import group_impression

            return {"ok": True, "data": group_impression.get_all()}
        except Exception as e:
            logger.error(f"impressions 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/entries", include_in_schema=False)
    async def api_entries():
        try:
            from . import self_memory

            entries = self_memory.load_entries()
            archive = _read_json(_STORE_DIR / "archive.json", [])
            return {"ok": True, "entries": entries, "archive_count": len(archive)}
        except Exception as e:
            logger.error(f"entries 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/journal", include_in_schema=False)
    async def api_journal(limit: int = 60):
        try:
            lines = _tail_file(_STORE_DIR / "journal.jsonl", limit)
            items = []
            for raw in lines:
                try:
                    items.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
            return {"ok": True, "items": items[-limit:]}
        except Exception as e:
            logger.error(f"journal 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/person_cards", include_in_schema=False)
    async def api_person_cards():
        """[方案L二期A] 人物卡全量（记忆档案 tab）。"""
        try:
            from . import person_cards

            cards = person_cards.load_all_cards()
            data = []
            for qq, card in cards.items():
                data.append(
                    {
                        "qq": str(qq),
                        "name": str(card.get("name", "") or qq),
                        "impression": str(card.get("impression", "") or ""),
                        "interaction": str(card.get("interaction", "") or ""),
                        "pet_peeves": [str(p) for p in (card.get("pet_peeves") or [])],
                        "interests": [str(i) for i in (card.get("interests") or [])],
                        "events_count": len(card.get("events") or []),
                        "events": card.get("events") or [],
                        "updated_at": float(card.get("updated_at", 0) or 0),
                    }
                )
            data.sort(key=lambda c: -c["updated_at"])
            return {"ok": True, "cards": data}
        except Exception as e:
            logger.error(f"person_cards 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/episodic_cards", include_in_schema=False)
    async def api_episodic_cards(limit: int = 100):
        """[方案L二期A] 情节卡尾部 limit 条（按 created_at 降序）。"""
        try:
            from . import episodic_cards as _ep

            _ep._load_if_needed()
            with _ep._lock:
                snapshot = [dict(c) for c in _ep._cache.values()]
            snapshot.sort(key=lambda c: -float(c.get("created_at", 0) or 0))
            return {"ok": True, "cards": snapshot[: max(1, min(500, int(limit or 100)))]}
        except Exception as e:
            logger.error(f"episodic_cards 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.post("/api/k/person_cards/update", include_in_schema=False)
    async def api_person_cards_update(token: str = Query(default=""), body: Dict[str, Any] = None):
        """[方案L二期A] 面板改卡（印象/相处/雷点），带 token 才放行。找不到卡返回 404。"""
        if not _token_ok(token):
            return JSONResponse(status_code=403, content={"ok": False, "error": "token 校验失败"})
        try:
            body = body or {}
            qq = str(body.get("qq", "") or "").strip()
            if not qq:
                return {"ok": False, "error": "缺少 qq"}
            from . import person_cards

            card = person_cards.get_card(qq)
            if card is None:
                return JSONResponse(status_code=404, content={"ok": False, "error": f"人物卡不存在: {qq}"})
            if "impression" in body:
                card["impression"] = str(body.get("impression") or "")[:200]
            if "interaction" in body:
                card["interaction"] = str(body.get("interaction") or "")[:200]
            if "pet_peeves" in body:
                pp = body.get("pet_peeves")
                if isinstance(pp, list):
                    merged = []
                    for p in pp:
                        p = str(p).strip()[:20]
                        if p and p not in merged:
                            merged.append(p)
                    card["pet_peeves"] = merged[:6]
            if not person_cards.save_card(card):
                return {"ok": False, "error": "写卡失败"}
            return {"ok": True}
        except Exception as e:
            logger.error(f"person_cards/update 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/memos", include_in_schema=False)
    async def api_memos():
        try:
            from ..heartFC_chat.handoff_memo import snapshot

            return {"ok": True, "data": snapshot()}
        except Exception as e:
            logger.error(f"memos 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/sessions", include_in_schema=False)
    async def api_sessions():
        try:
            from ..heartFC_chat.session_pool import session_pool

            return {"ok": True, "data": session_pool.snapshot()}
        except Exception as e:
            logger.error(f"sessions 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/mood", include_in_schema=False)
    async def api_mood():
        try:
            from ..moods.moods import MoodManager

            mood = MoodManager.get_instance()
            cur = getattr(mood, "current_mood", None)
            return {
                "ok": True,
                "valence": round(float(getattr(cur, "valence", 0) or 0), 3),
                "arousal": round(float(getattr(cur, "arousal", 0) or 0), 3),
                "reason": str(getattr(mood, "last_reason", "") or ""),
            }
        except Exception as e:
            logger.error(f"mood 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/reflections", include_in_schema=False)
    async def api_reflections():
        try:
            files = sorted(_LOG_ROOT.glob("reflection_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
            return {"ok": True, "files": [p.name for p in files[:30]]}
        except Exception as e:
            logger.error(f"reflections 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/reflection", include_in_schema=False)
    async def api_reflection(name: str = ""):
        try:
            if not re.fullmatch(r"reflection_\d{8}\.md", name or ""):
                return {"ok": False, "error": "非法文件名"}
            path = _LOG_ROOT / name
            text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else "（文件不存在）"
            return {"ok": True, "name": name, "content": text}
        except Exception as e:
            logger.error(f"reflection 内容异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/log_modules", include_in_schema=False)
    async def api_log_modules():
        try:
            mods = sorted(d.name for d in _LOG_ROOT.iterdir() if d.is_dir()) if _LOG_ROOT.exists() else []
            return {"ok": True, "modules": mods}
        except Exception as e:
            logger.error(f"log_modules 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/modules_status", include_in_schema=False)
    async def api_modules_status():
        """全板块运行脉搏：每个模块的日志目录 → 最后活跃时间 + 今日文件大小（原版+魔改全覆盖）。"""
        try:
            result = []
            if _LOG_ROOT.exists():
                for d in sorted(_LOG_ROOT.iterdir()):
                    if not d.is_dir():
                        continue
                    files = list(d.glob("*.log"))
                    if not files:
                        continue
                    newest = max(files, key=lambda p: p.stat().st_mtime)
                    st = newest.stat()
                    result.append(
                        {
                            "module": d.name,
                            "last_active": st.st_mtime,
                            "size_kb": round(st.st_size / 1024, 1),
                        }
                    )
            result.sort(key=lambda x: -x["last_active"])
            return {"ok": True, "modules": result}
        except Exception as e:
            logger.error(f"modules_status 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/logs", include_in_schema=False)
    async def api_logs(module: str = "other", lines: int = 120):
        try:
            if not _MODULE_NAME_RE.fullmatch(module or ""):
                return {"ok": False, "error": "非法模块名"}
            mod_dir = _LOG_ROOT / module
            if not mod_dir.is_dir():
                return {"ok": True, "lines": [], "file": ""}
            candidates = list(mod_dir.glob("*.log"))
            if not candidates:
                return {"ok": True, "lines": [], "file": ""}
            newest = max(candidates, key=lambda p: p.stat().st_mtime)
            content = _tail_file(newest, lines)
            return {"ok": True, "file": str(newest), "lines": [ln.rstrip("\n") for ln in content]}
        except Exception as e:
            logger.error(f"logs 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/score_trend", include_in_schema=False)
    async def api_score_trend(days: int = 1):
        """打分趋势：1天按小时桶，2天按6小时桶，3-7天按天桶。返回均分+通过率序列。"""
        try:
            from . import score_events

            days = max(1, min(days, 7))
            events = score_events.get_recent(days=days)
            bucket = 3600 if days == 1 else (21600 if days == 2 else 86400)
            buckets: Dict[int, Dict[str, float]] = {}
            for e in events:
                ts = float(e.get("ts", 0) or 0)
                b = int(ts // bucket) * bucket
                slot = buckets.setdefault(b, {"n": 0, "passed": 0, "sum": 0.0})
                slot["n"] += 1
                slot["passed"] += 1 if e.get("passed") else 0
                slot["sum"] += float(e.get("score", 5) or 5)
            fmt = "%m-%d" if bucket >= 86400 else "%m-%d %H:%M"
            trend = [
                {
                    "t": time.strftime(fmt, time.localtime(b)),
                    "avg": round(v["sum"] / v["n"], 2) if v["n"] else 0,
                    "pass_rate": round(v["passed"] / v["n"] * 100, 1) if v["n"] else 0,
                    "n": int(v["n"]),
                }
                for b, v in sorted(buckets.items())
            ]
            return {"ok": True, "trend": trend}
        except Exception as e:
            logger.error(f"score_trend 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/reason_dist", include_in_schema=False)
    async def api_reason_dist(days: int = 1):
        """拒发原因分布：按审核理由关键词归类（较真/空话/时效/重复/场合/其他）。"""
        try:
            from . import score_events

            events = score_events.get_recent(days=max(1, min(days, 7)))
            dist: Dict[str, int] = {}
            rejected = 0
            for e in events:
                if e.get("passed"):
                    continue
                rejected += 1
                reason = str(e.get("reason", "") or "")
                # 分类器兼容新旧两种 reason 格式（新格式形如"违反9.9禁评价"）
                for kw, label in (
                    ("较真", "较真"), ("禁评价", "评价别人"), ("9.9", "评价别人"),
                    ("空话", "空话附和"), ("太XX", "空话附和"),
                    ("时效", "时效"), ("重复", "重复主语"), ("主语", "重复主语"),
                    ("场合", "场合"), ("分段", "碎片化"), ("AI味", "AI味"),
                    ("虚构", "虚构"), ("动作真实性", "吹牛"), ("at_qq", "格式污染"),
                    ("口语化", "机械赞同"), ("客服", "客服腔"),
                ):
                    if kw in reason:
                        dist[label] = dist.get(label, 0) + 1
                        break
                else:
                    dist["其他"] = dist.get("其他", 0) + 1
            return {"ok": True, "rejected": rejected, "dist": sorted(dist.items(), key=lambda kv: -kv[1])}
        except Exception as e:
            logger.error(f"reason_dist 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/mood_curve", include_in_schema=False)
    async def api_mood_curve(lines: int = 1440):
        """心情心电图：从 logs/mood/ 最新日志解析绝对愉悦度/唤醒度时间序列（衰减循环每分钟打点）。"""
        try:
            mod_dir = _LOG_ROOT / "mood"
            candidates = list(mod_dir.glob("*.log")) if mod_dir.is_dir() else []
            if not candidates:
                return {"ok": True, "series": [], "file": ""}
            newest = max(candidates, key=lambda p: p.stat().st_mtime)
            pat = re.compile(r"(\d{2}-\d{2} \d{2}:\d{2}).*?愉悦度:\s*(-?[\d.]+).*?唤醒度:\s*(-?[\d.]+)")
            series = []
            for raw in _tail_file(newest, max(60, min(lines, 1440))):
                m = pat.search(raw)
                if m:
                    series.append({"t": m.group(1), "v": float(m.group(2)), "a": float(m.group(3))})
            return {"ok": True, "file": str(newest), "series": series[-lines:]}
        except Exception as e:
            logger.error(f"mood_curve 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/errors", include_in_schema=False)
    async def api_errors(lines_per_mod: int = 300):
        """报错扫描：各模块最新日志尾部抓 ERROR/Traceback/异常/失败/错误 行。"""
        try:
            err_re = re.compile(r"ERROR|Traceback|异常|失败|错误")
            out: List[Dict[str, str]] = []
            if _LOG_ROOT.exists():
                for d in sorted(_LOG_ROOT.iterdir()):
                    if not d.is_dir():
                        continue
                    files = list(d.glob("*.log"))
                    if not files:
                        continue
                    newest = max(files, key=lambda p: p.stat().st_mtime)
                    for ln in _tail_file(newest, max(50, min(lines_per_mod, 500))):
                        if err_re.search(ln):
                            out.append({"module": d.name, "line": ln.strip()[:300]})
            return {"ok": True, "errors": out[-80:]}
        except Exception as e:
            logger.error(f"errors 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/bills", include_in_schema=False)
    async def api_bills(days: int = 7):
        """token 账单：聚合 MongoDB llm_usage（按天/按模型/按请求类型的成本与 token）。"""
        try:
            from datetime import datetime, timedelta

            from src.common.database import db

            start = datetime.now() - timedelta(days=max(1, min(days, 30)))
            day_expr = {"$dateToString": {"format": "%m-%d", "date": "$timestamp"}}

            def agg(group_fields: Dict[str, Any]):
                pipeline = [
                    {"$match": {"timestamp": {"$gte": start}}},
                    {
                        "$group": {
                            "_id": group_fields,
                            "cost": {"$sum": {"$ifNull": ["$cost", 0]}},
                            "tokens": {"$sum": {"$add": [{"$ifNull": ["$prompt_tokens", 0]}, {"$ifNull": ["$completion_tokens", 0]}]}},
                            "n": {"$sum": 1},
                        }
                    },
                    {"$sort": {"_id.day": 1}},
                ]
                return list(db.llm_usage.aggregate(pipeline))

            days_map: Dict[str, Dict[str, Any]] = {}
            models: Dict[str, float] = {}
            for r in agg({"day": day_expr, "model": "$model_name"}):
                day = (r["_id"] or {}).get("day", "?")
                model = (r["_id"] or {}).get("model") or "?"
                cost = round(float(r.get("cost", 0) or 0), 4)
                slot = days_map.setdefault(day, {"cost": 0.0, "tokens": 0, "n": 0})
                slot["cost"] = round(slot["cost"] + cost, 4)
                slot["tokens"] += int(r.get("tokens", 0) or 0)
                slot["n"] += int(r.get("n", 0) or 0)
                models[model] = round(models.get(model, 0) + cost, 4)

            types: Dict[str, float] = {}
            for r in agg({"day": day_expr, "type": "$request_type"}):
                rtype = (r["_id"] or {}).get("type") or "?"
                types[rtype] = round(types.get(rtype, 0) + float(r.get("cost", 0) or 0), 4)

            by_day = [{"day": d, **v} for d, v in sorted(days_map.items())]
            total = round(sum(v["cost"] for v in days_map.values()), 2)
            return {
                "ok": True,
                "days": max(1, min(days, 30)),
                "by_day": by_day,
                "by_model": sorted(models.items(), key=lambda kv: -kv[1]),
                "by_type": sorted(types.items(), key=lambda kv: -kv[1]),
                "total": total,
            }
        except Exception as e:
            logger.error(f"bills 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/purpose", include_in_schema=False)
    async def api_purpose():
        """[M3.2 S1/S3] 动机池状态：今日额度/降级额度/pending 池/结算流水/收尾惦记。"""
        try:
            from .purpose_pool import BROWSE_DAILY_MAX, CLOSURE_COOLDOWN_MINUTES, PENDING_TTL_DAYS, PURPOSE_DAILY_MAX

            now = time.time()
            today = time.strftime("%Y-%m-%d")
            pp = _read_json(_STORE_DIR / "purpose_pool.json", {})
            if pp.get("day") != today:  # 跨天未重置=今天还没跑过，展示归零视图
                pp = {"day": today, "count_today": 0, "count_browse_today": 0}
            pending = []
            for p in (pp.get("pending") or [])[:12]:
                sid = str(p.get("origin_stream", "") or "")
                pending.append(
                    {
                        "summary": str(p.get("summary", "") or ""),
                        "next_opening": str(p.get("next_opening", "") or ""),
                        "source": "收尾惦记" if sid else "闲逛流露",
                        "origin": sid[-10:] if sid else "",
                        "hits": int(p.get("hits", 0) or 0),
                        "remain_days": round(max(0.0, (float(p.get("expires_at", 0) or 0) - now) / 86400), 1),
                        "expired": float(p.get("expires_at", 0) or 0) <= now,
                    }
                )
            history = [
                {
                    "time": time.strftime("%m-%d %H:%M", time.localtime(float(h.get("at", 0) or 0))),
                    "outcome": str(h.get("outcome", "") or ""),
                    "summary": str(h.get("summary", "") or ""),
                }
                for h in (pp.get("history") or [])[-12:]
            ]
            closure_at = {
                sid[-10:]: time.strftime("%m-%d %H:%M", time.localtime(float(ts or 0)))
                for sid, ts in (pp.get("closure_at") or {}).items()
            }
            return {
                "ok": True,
                "day": today,
                "count_today": int(pp.get("count_today", 0) or 0),
                "daily_max": PURPOSE_DAILY_MAX,
                "browse_today": int(pp.get("count_browse_today", 0) or 0),
                "browse_max": BROWSE_DAILY_MAX,
                "pending_ttl_days": PENDING_TTL_DAYS,
                "closure_cooldown_min": CLOSURE_COOLDOWN_MINUTES,
                "pending": pending,
                "history": history,
                "closure_at": closure_at,
            }
        except Exception as e:
            logger.error(f"purpose 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/budget", include_in_schema=False)
    async def api_budget():
        """[M3.2 S2] 今日预算闸门：实时用量（内存累加器）/预算/三档状态/80%提醒状态。"""
        try:
            from ...common.token_budget import get_today_usage, parse_budget

            daily, ratio = parse_budget(
                getattr(global_config, "token_budget_daily_cny", None),
                getattr(global_config, "token_budget_alert_ratio", None),
            )
            usage = get_today_usage()
            used = float(usage.get("cny", 0.0) or 0)
            if daily <= 0:
                tier = "unlimited"
            elif used >= daily:
                tier = "capped"
            elif used >= daily * ratio:
                tier = "warning"
            else:
                tier = "ok"
            alert = _read_json(Path("data") / "token_alert.json", None)
            if isinstance(alert, dict) and alert.get("day") != time.strftime("%Y-%m-%d"):
                alert = None
            return {
                "ok": True,
                "used": round(used, 2),
                "in_tokens": int(usage.get("in", 0) or 0),
                "out_tokens": int(usage.get("out", 0) or 0),
                "daily": daily,
                "ratio": ratio,
                "tier": tier,
                "alert": alert,
            }
        except Exception as e:
            logger.error(f"budget 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/world_cards", include_in_schema=False)
    async def api_world_cards():
        """[P4] 世界卡（她上网学到的事实知识，闲逛时注入）。尾部 30 条按 created_at 降序。"""
        try:
            from . import world_cards as _wc

            _wc._load_if_needed()
            with _wc._lock:
                snapshot = [dict(c) for c in _wc._cache.values()]
            snapshot.sort(key=lambda c: -float(c.get("created_at", 0) or 0))
            return {"ok": True, "cards": snapshot[:30], "total": len(snapshot)}
        except Exception as e:
            logger.error(f"world_cards 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/config", include_in_schema=False)
    async def api_config():
        """系统配置面板：只读展示 bot_config.toml 全量（token 脱敏）。"""
        try:
            import tomli

            path = Path(__file__).parents[3] / "config" / "bot_config.toml"
            data = tomli.load(open(path, "rb")) if path.exists() else {}
            pm = data.get("plastic_memory")
            if isinstance(pm, dict) and pm.get("dashboard_token"):
                pm["dashboard_token"] = "***已设置***"
            return {"ok": True, "path": str(path), "data": data}
        except Exception as e:
            logger.error(f"config 读取异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/imp_history", include_in_schema=False)
    async def api_imp_history(stream_id: str = "", limit: int = 40):
        """群像认知演变：journal 里该群的 group_update / manual_edit 时间线。"""
        try:
            items: List[Dict[str, Any]] = []
            for raw in _tail_file(_STORE_DIR / "journal.jsonl", 400):
                try:
                    x = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                det = x.get("detail") or {}
                if x.get("op") in ("group_update", "manual_edit") and det.get("stream_id") == stream_id:
                    items.append(x)
            return {"ok": True, "items": items[-max(1, min(limit, 100)):]}
        except Exception as e:
            logger.error(f"imp_history 异常: {e}")
            return {"ok": False, "error": str(e)}

    # ---------- 操作接口（token 保护） ----------

    @app.post("/api/k/action/reflect", include_in_schema=False)
    async def api_action_reflect(token: str = Query(default="")):
        if not _token_ok(token):
            return _deny()
        from .reflection import run_daily_reflection

        result = await run_daily_reflection()
        return {"ok": True, "result": result}

    @app.post("/api/k/action/review", include_in_schema=False)
    async def api_action_review(token: str = Query(default=""), stream_id: str = Query(default="")):
        if not _token_ok(token):
            return _deny()
        if not stream_id:
            return {"ok": False, "error": "缺少 stream_id"}
        from .group_reviewer import review_group

        group_name = ""
        try:
            from src.plugins.chat.chat_stream import chat_manager

            group_name = chat_manager.get_stream_name(stream_id) or ""
        except Exception:
            pass
        await review_group(stream_id, group_name)
        return {"ok": True, "msg": f"已触发群像审查: {group_name or stream_id}"}

    @app.post("/api/k/action/cleanup", include_in_schema=False)
    async def api_action_cleanup(token: str = Query(default=""), days: int = 30):
        if not _token_ok(token):
            return _deny()
        from .score_events import cleanup

        removed = cleanup(max(1, min(days, 365)))
        return {"ok": True, "removed": removed}

    @app.post("/api/k/entries/delete", include_in_schema=False)
    async def api_entries_delete(token: str = Query(default=""), body: Dict[str, Any] = None):
        if not _token_ok(token):
            return _deny()
        content = str((body or {}).get("content", "") or "").strip()
        if not content:
            return {"ok": False, "error": "缺少 content"}
        from . import self_memory

        removed = self_memory.remove_entry(content)
        return {"ok": bool(removed), "msg": "已移除（进归档区可恢复）" if removed else "未找到精确匹配条目"}

    @app.post("/api/k/impressions/set", include_in_schema=False)
    async def api_impressions_set(token: str = Query(default=""), body: Dict[str, Any] = None):
        if not _token_ok(token):
            return _deny()
        body = body or {}
        stream_id = str(body.get("stream_id", "") or "")
        field = str(body.get("field", "") or "")
        value = body.get("value")
        from . import group_impression

        ok = group_impression.set_field(stream_id, field, value)
        return {"ok": ok, "msg": f"已修正 {field}" if ok else "修正失败（字段/值/群不存在）"}

    @app.post("/api/k/action/memo", include_in_schema=False)
    async def api_action_memo(token: str = Query(default=""), body: Dict[str, Any] = None):
        """调试用：手动塞一份测试 memo，验证层4交接机制。"""
        if not _token_ok(token):
            return _deny()
        body = body or {}
        sid = str(body.get("stream_id", "") or "").strip()
        text = str(body.get("text", "") or "").strip()
        if not sid or not text:
            return {"ok": False, "error": "缺少 stream_id 或 text"}
        from .handoff_memo import force_memo

        force_memo(sid, text)
        return {"ok": True, "msg": "调试 memo 已注入"}

    # ---------- 集权·配置热改（白名单 + 注释保护） ----------

    @app.get("/api/k/config_editable", include_in_schema=False)
    async def api_config_editable():
        try:
            import tomli

            path = Path(__file__).parents[3] / "config" / "bot_config.toml"
            data = tomli.load(open(path, "rb")) if path.exists() else {}
            out: Dict[str, Any] = {}
            for sec, keys in _EDITABLE.items():
                sec_data = data.get(sec) or {}
                out[sec] = {}
                for key, meta in keys.items():
                    out[sec][key] = {
                        "kind": meta["kind"],
                        "value": sec_data.get(key),
                        "hot": bool(meta["attr"]),
                        "range": list(meta["rng"]) if meta["rng"] else None,
                        "label": meta["label"],
                        "desc": meta["desc"],
                    }
            return {"ok": True, "editable": out}
        except Exception as e:
            logger.error(f"config_editable 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.post("/api/k/config/set", include_in_schema=False)
    async def api_config_set(token: str = Query(default=""), body: Dict[str, Any] = None):
        """白名单内标量热改：文本手术保注释 + tomli 校验 + hot 字段 setattr 立即生效。"""
        if not _token_ok(token):
            return _deny()
        body = body or {}
        section = str(body.get("section", "") or "")
        key = str(body.get("key", "") or "")
        value = body.get("value")
        meta = _EDITABLE.get(section, {}).get(key)
        if not meta:
            return {"ok": False, "error": "该字段不在可编辑白名单"}
        kind, attr, rng = meta["kind"], meta["attr"], meta["rng"]
        try:
            if kind == "bool":
                coerced = value if isinstance(value, bool) else str(value).strip().lower() in ("true", "1", "是", "on", "y")
                literal = "true" if coerced else "false"
            elif kind == "int":
                coerced = int(float(value))
                if rng:
                    coerced = max(rng[0], min(rng[1], coerced))
                literal = str(coerced)
            elif kind == "float":
                coerced = float(value)
                if rng:
                    coerced = max(rng[0], min(rng[1], coerced))
                literal = repr(coerced)
            else:
                coerced = str(value).strip()
                if not coerced:
                    return {"ok": False, "error": "值不能为空"}
                literal = json.dumps(coerced, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            return {"ok": False, "error": f"值格式错误: {e}"}

        path = Path(__file__).parents[3] / "config" / "bot_config.toml"
        try:
            text = path.read_text(encoding="utf-8")
            new_text = _toml_set_scalar(text, section, key, literal)
            import tomli as _tomli

            _tomli.loads(new_text)  # 写前校验，坏了就不落盘
            path.write_text(new_text, encoding="utf-8")
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.error(f"配置写入失败: {e}")
            return {"ok": False, "error": f"配置写入失败: {e}"}

        hot = False
        if attr:
            setattr(global_config, attr, coerced)
            hot = True
        return {
            "ok": True,
            "msg": f"{section}.{key} 已保存（{'立即生效' if hot else '重启后生效'}）",
            "hot": hot,
            "value": coerced,
        }

    # ---------- 集权·记忆&数据库 ----------

    @app.get("/api/k/db/overview", include_in_schema=False)
    async def api_db_overview():
        try:
            from src.common.database import db

            counts: Dict[str, int] = {}
            for name in ("graph_data.nodes", "graph_data.edges", "messages", "person_info", "knowledges", "llm_usage"):
                try:
                    counts[name] = db[name].count_documents({})
                except Exception:
                    counts[name] = -1
            return {"ok": True, "counts": counts}
        except Exception as e:
            logger.error(f"db overview 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/db/memory", include_in_schema=False)
    async def api_db_memory(q: str = "", page: int = 1, size: int = 20):
        """海马体记忆图谱节点浏览/搜索。"""
        try:
            from src.common.database import db

            filt = {"concept": {"$regex": re.escape(q), "$options": "i"}} if q else {}
            total = db.graph_data.nodes.count_documents(filt)
            page = max(1, page)
            cur = (
                db.graph_data.nodes.find(filt, {"concept": 1, "memory_items": 1, "created_time": 1, "last_modified": 1})
                .sort("last_modified", -1)
                .skip((page - 1) * size)
                .limit(size)
            )
            items = [
                {
                    "id": str(d["_id"]),
                    "concept": str(d.get("concept", "")),
                    "memory_items": [str(x)[:200] for x in (d.get("memory_items") or [])][:20],
                    "last_modified": float(d.get("last_modified", 0) or 0),
                }
                for d in cur
            ]
            return {"ok": True, "total": total, "page": page, "items": items}
        except Exception as e:
            logger.error(f"db memory 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.post("/api/k/db/memory/update", include_in_schema=False)
    async def api_db_memory_update(token: str = Query(default=""), body: Dict[str, Any] = None):
        if not _token_ok(token):
            return _deny()
        body = body or {}
        from bson import ObjectId

        from src.common.database import db

        try:
            oid = ObjectId(str(body.get("id", "") or ""))
        except Exception:
            return {"ok": False, "error": "非法 id"}
        items = body.get("memory_items")
        if not isinstance(items, list):
            return {"ok": False, "error": "memory_items 必须是列表"}
        items = [str(x).strip()[:200] for x in items if str(x).strip()][:20]
        if not items:
            return {"ok": False, "error": "至少保留一条记忆内容"}
        db.graph_data.nodes.update_one({"_id": oid}, {"$set": {"memory_items": items, "last_modified": time.time()}})
        try:
            from .self_memory import write_journal

            write_journal("memory_edit", {"id": str(oid), "items": items[:3]})
        except Exception:
            pass
        return {"ok": True, "msg": f"记忆节点已更新（{len(items)} 条）"}

    @app.post("/api/k/db/memory/delete", include_in_schema=False)
    async def api_db_memory_delete(token: str = Query(default=""), body: Dict[str, Any] = None):
        if not _token_ok(token):
            return _deny()
        body = body or {}
        from bson import ObjectId

        from src.common.database import db

        try:
            oid = ObjectId(str(body.get("id", "") or ""))
        except Exception:
            return {"ok": False, "error": "非法 id"}
        node = db.graph_data.nodes.find_one({"_id": oid}, {"concept": 1})
        res = db.graph_data.nodes.delete_one({"_id": oid})
        try:
            from .self_memory import write_journal

            write_journal("memory_delete", {"id": str(oid), "concept": str((node or {}).get("concept", ""))})
        except Exception:
            pass
        return {"ok": bool(res.deleted_count), "msg": "记忆节点已删除"}

    @app.get("/api/k/db/chats", include_in_schema=False)
    async def api_db_chats(limit: int = 30):
        """会话列表（面板聊天记录页）：chat_id + 展示名 + 消息量 + 最后活跃。"""
        try:
            from src.common.database import db

            getter = None
            try:
                from src.plugins.chat.chat_stream import chat_manager

                getter = getattr(chat_manager, "get_stream_name", None)
            except Exception:
                getter = None
            pipeline = [
                {"$group": {"_id": "$chat_id", "count": {"$sum": 1}, "last": {"$max": "$time"}}},
                {"$sort": {"last": -1}},
                {"$limit": max(1, min(limit, 100))},
            ]
            items = []
            for r in db.messages.aggregate(pipeline):
                cid = str(r.get("_id") or "")
                name = ""
                if getter:
                    try:
                        name = str(getter(cid) or "")
                    except Exception:
                        name = ""
                items.append(
                    {
                        "chat_id": cid,
                        "name": name or ("会话 " + cid[-8:] if cid else "未知会话"),
                        "count": int(r.get("count", 0) or 0),
                        "last": float(r.get("last", 0) or 0),
                    }
                )
            return {"ok": True, "items": items}
        except Exception as e:
            logger.error(f"db chats 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/db/messages", include_in_schema=False)
    async def api_db_messages(q: str = "", chat_id: str = "", page: int = 1, size: int = 30):
        """消息记录浏览/搜索（只读）。支持按会话过滤（面板聊天记录页）。"""
        try:
            from src.common.database import db

            filt: Dict[str, Any] = {}
            if chat_id:
                filt["chat_id"] = chat_id
            if q:
                filt["processed_plain_text"] = {"$regex": re.escape(q), "$options": "i"}
            total = db.messages.count_documents(filt)
            page = max(1, page)
            cur = (
                db.messages.find(filt, {"time": 1, "chat_id": 1, "user_info": 1, "processed_plain_text": 1})
                .sort("time", -1)
                .skip((page - 1) * 30)
                .limit(max(1, min(size, 100)))
            )
            items = [
                {
                    "time": float(d.get("time", 0) or 0),
                    "chat_id": str(d.get("chat_id", "") or ""),
                    "who": str((d.get("user_info") or {}).get("user_nickname") or "?"),
                    "uid": str((d.get("user_info") or {}).get("user_id", "") or ""),
                    "text": str(d.get("processed_plain_text", "") or "")[:150],
                }
                for d in cur
            ]
            return {"ok": True, "total": total, "page": page, "items": items}
        except Exception as e:
            logger.error(f"db messages 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.get("/api/k/person_info", include_in_schema=False)
    async def api_person_info(q: str = "", page: int = 1):
        """用户印象（person_info）：名字/好感度/认识时长，按好感排序。"""
        try:
            from src.common.database import db

            filt = {"person_name": {"$regex": re.escape(q), "$options": "i"}} if q else {}
            total = db.person_info.count_documents(filt)
            page = max(1, page)
            cur = (
                db.person_info.find(filt, {"person_id": 1, "person_name": 1, "relationship_value": 1, "konw_time": 1, "msg_interval": 1})
                .sort("relationship_value", -1)
                .skip((page - 1) * 20)
                .limit(20)
            )
            items = [
                {
                    "person_id": str(d.get("person_id", "") or ""),
                    "name": str(d.get("person_name", "") or "?"),
                    "relationship_value": float(d.get("relationship_value", 0) or 0),
                    "konw_time": float(d.get("konw_time", 0) or 0),
                    "msg_interval": int(d.get("msg_interval", 0) or 0),
                }
                for d in cur
            ]
            return {"ok": True, "total": total, "page": page, "items": items}
        except Exception as e:
            logger.error(f"person_info 异常: {e}")
            return {"ok": False, "error": str(e)}

    @app.post("/api/k/db/person/update", include_in_schema=False)
    async def api_db_person_update(token: str = Query(default=""), body: Dict[str, Any] = None):
        """人工修正某人好感度（即时生效：好感度系统每次都从库读）。"""
        if not _token_ok(token):
            return _deny()
        body = body or {}
        from src.common.database import db

        pid = str(body.get("person_id", "") or "").strip()
        if not pid:
            return {"ok": False, "error": "缺少 person_id"}
        try:
            value = max(-1000.0, min(1000.0, float(body.get("relationship_value", 0) or 0)))
        except (TypeError, ValueError):
            return {"ok": False, "error": "好感度必须是数字"}
        db.person_info.update_one({"person_id": pid}, {"$set": {"relationship_value": value}})
        try:
            from .self_memory import write_journal

            write_journal("person_edit", {"person_id": pid, "relationship_value": value})
        except Exception:
            pass
        return {"ok": True, "msg": f"好感度已修正为 {value:.0f}（立即生效）"}

    @app.get("/api/k/schedule", include_in_schema=False)
    async def api_schedule():
        """今日日程（只读，来自 db.schedule）。"""
        try:
            from datetime import datetime as _dt

            from src.common.database import db

            today = _dt.now().strftime("%Y-%m-%d")
            doc = db.schedule.find_one({"date": today}) or {}

            def _fmt_task(t):
                """真机实锤 2026-10-30：today_done_list 元素是 [datetime, 文本] 结构，str() 直接泄漏
                datetime repr——整形成 HH:MM 文本。"""
                if isinstance(t, (list, tuple)):
                    head, parts = "", []
                    for x in t:
                        if isinstance(x, _dt):
                            head = x.strftime("%H:%M")
                        elif isinstance(x, str):
                            parts.append(x)
                    line = (head + " " + " ".join(parts)).strip() if head else " ".join(parts)
                    return line[:120] or "（空日程项）"
                return str(t)[:120]

            tasks = [_fmt_task(t) for t in (doc.get("today_done_list") or [])][:20]
            return {
                "ok": True,
                "date": today,
                "enabled": bool(getattr(global_config, "enable_schedule_gen", False)),
                "tasks": tasks,
            }
        except Exception as e:
            logger.error(f"schedule 异常: {e}")
            return {"ok": False, "error": str(e)}

    logger.info("看板路由已注册: /dashboard")
