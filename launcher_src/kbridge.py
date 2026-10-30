# -*- coding: utf-8 -*-
"""
面板实化桥（K桥）· 2026-10-30
================================
面板「实化」的 launcher 侧引擎，js_api 透传用：
  · kget/kpost      —— 代理 MaiCore 8000 端口的 /api/k/*（读接口免token；操作接口自动带上
                       bot_config [plastic_memory] dashboard_token，同机读文件拿，不经过页面）
  · cfg_get/cfg_save —— bot_config.toml 文本手术（只改值不动注释，与 K面板 _toml_set_scalar 同款；
                       写前 tomllib 校验；保存后可选自动重启编排：停全家桶→写→拉起）
  · env_set          —— .env KEY=VALUE 行替换（SILICONFLOW_KEY / DEEP_SEEK_* 等）
  · persona_*        —— 人设页读写（bot.nickname / personality / identity）
  · wizard_save      —— 首启向导真写入（QQ+Key+BaseURL+DeepSeek Key，复刻 config_manager 落点：
                       bot_config [bot].qq + .env SILICONFLOW_KEY + lpmm llm_providers 同步）
  · flow_status      —— 心流监控聚合（events.jsonl 按 stream_id 聚合 interest/最后活跃/消息数）
全部 fail-open：任何异常都返回 {"ok":False,...}，绝不炸面板。
"""
import json
import re
import time
import urllib.request
from pathlib import Path

try:
    import tomllib  # Python 3.11+ 标准库（PyInstaller 内嵌 3.12 自带）
except ImportError:  # pragma: no cover
    tomllib = None

from config_paths import MAIBOT_DIR

CFG_PATH = MAIBOT_DIR / "config" / "bot_config.toml"
LPMM_PATH = MAIBOT_DIR / "config" / "lpmm_config.toml"
ADAPTER_CFG = MAIBOT_DIR / "MaiBot-Napcat-Adapter" / "config.toml"
ENV_PATH = MAIBOT_DIR / ".env"
NAPCAT_DIR = MAIBOT_DIR / "napcat"
K_BASE = "http://127.0.0.1:8000"

# K面板热改白名单（dashboard.py _EDITABLE 的 attr 非空 = 立即生效字段）
HOT_FIELDS = {
    ("token_budget", "daily_cny"), ("token_budget", "alert_ratio"),
    ("plastic_memory", "enable"), ("plastic_memory", "group_review_enabled"),
    ("plastic_memory", "group_review_hour"), ("plastic_memory", "reflection_enabled"),
    ("plastic_memory", "reflection_hour"), ("plastic_memory", "score_retention_days"),
    ("plastic_memory", "handoff_min_gap_min"), ("plastic_memory", "handoff_max_gap_min"),
    ("plastic_memory", "dashboard_token"),
    ("person_cards", "enable"), ("person_cards", "archive_hour"),
    ("person_cards", "episodic_enable"), ("person_cards", "episodic_inject_num"),
    ("experimental", "group_unified_brain"), ("experimental", "burst_session"),
    ("experimental", "burst_idle_min"), ("experimental", "burst_max_tokens"),
}


def _ev_ts(v):
    """events.jsonl 的 ts 兼容解析：unix float 或 ISO 字符串（event_bus 写的是 ISO）——
    真机实锤 2026-10-30：float(ISO) 裸抛 ValueError 让 day_stats 整体失败，面板退回演示数据。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        try:
            from datetime import datetime

            return datetime.fromisoformat(str(v)).timestamp()
        except Exception:
            return 0.0


def _ok(**kw):
    d = {"ok": True}
    d.update(kw)
    return d


def _err(msg, **kw):
    d = {"ok": False, "error": str(msg)}
    d.update(kw)
    return d


def _http(method, path, body=None, token="", timeout=5.0):
    url = K_BASE + path
    data = None
    headers = {"Content-Type": "application/json"}
    if token:
        sep = "&" if "?" in url else "?"
        url = "{}{}token={}".format(url, sep, urllib.request.quote(token))
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _dash_token():
    """从 bot_config.toml 读看板令牌（操作接口要用）。"""
    try:
        data = tomllib.loads(CFG_PATH.read_text(encoding="utf-8"))
        return str(((data.get("plastic_memory") or {}).get("dashboard_token")) or "")
    except Exception:
        return ""


def _toml_set_scalar(text, section, key, literal):
    """文本手术：只替换 [section] 块内 key 行的值，保留行尾注释与其余内容。
    与 dashboard.py _toml_set_scalar 同款（段头容忍行内注释；注释组必须"空格+#"开头）。"""
    lines = text.splitlines(keepends=True)
    sec_re = re.compile(r"^\s*\[" + re.escape(section) + r"\](\s*#.*)?$")
    key_re = re.compile(r"^(\s*" + re.escape(key) + r"\s*=\s*)(.*?)(\s+#.*)?$")
    out, in_sec, found = [], False, False
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
                out.append("{}{}{}\n".format(m.group(1), literal, m.group(3) or ""))
                found = True
                continue
        out.append(ln)
    if not found:
        raise ValueError("未在 [{}] 找到键 {}".format(section, key))
    return "".join(out)


def _set_key(text, section, key, literal):
    """文本手术 + 兜底：键已存在→原位替换（保注释）；不存在→段尾插入；段不存在→文件尾建段。
    （出厂 bot_config 没有 [plastic_memory]/[token_budget]/[tts] 段，首次保存时自动补段）"""
    try:
        return _toml_set_scalar(text, section, key, literal)
    except ValueError:
        lines = text.splitlines(keepends=True)
        if not lines or (lines[-1].strip() and not lines[-1].endswith("\n")):
            if lines:
                lines[-1] += "\n"
        sec_re = re.compile(r"^\s*\[" + re.escape(section) + r"\](\s*#.*)?$")
        start = None
        for i, ln in enumerate(lines):
            if sec_re.match(ln.rstrip("\n")):
                start = i
                break
        newline = "{} = {}\n".format(key, literal)
        if start is None:  # 建段
            lines.append("\n[{}]\n".format(section))
            lines.append(newline)
            return "".join(lines)
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if re.match(r"^\s*\[", lines[j]):
                end = j
                break
        ins = end
        while ins > start + 1 and not lines[ins - 1].strip():
            ins -= 1
        lines.insert(ins, newline)
        return "".join(lines)


def _literal(kind, value):
    """JS 值 → TOML 字面量（与 K面板 config/set 同规则；list 走 JSON，int 数组滤空）。"""
    if kind == "bool":
        b = value if isinstance(value, bool) else str(value).strip().lower() in ("true", "1", "是", "on", "y")
        return "true" if b else "false", b
    if kind == "int":
        n = int(float(value))
        return str(n), n
    if kind == "float":
        f = float(value)
        return repr(f), f
    if kind == "intlist":
        items = [int(float(x)) for x in re.split(r"[,，\s]+", str(value).strip()) if x.strip()]
        return "[" + ", ".join(str(i) for i in items) + "]", items
    if kind == "strlist":
        items = [x.strip() for x in re.split(r"[,，]+", str(value).strip()) if x.strip()]
        return "[" + ", ".join(json.dumps(x, ensure_ascii=False) for x in items) + "]", items
    s = str(value).strip()
    return json.dumps(s, ensure_ascii=False), s


class KBridge:
    """实例化时传 pm（ProcManager，重启编排用）与 log。全部方法 pywebview 可直调。"""

    def __init__(self, pm, log):
        self.pm = pm
        self.log = log

    # ---------- K API 代理 ----------
    def kget(self, path):
        try:
            return _http("GET", str(path))
        except Exception as exc:
            return _err("她还没上线（MaiCore 8000 不可达）：{}".format(exc))

    def kpost(self, path, body=None):
        try:
            return _http("POST", str(path), body=body or {}, token=_dash_token())
        except Exception as exc:
            return _err("操作失败：{}".format(exc))

    def test_provider(self, base_url, key):
        """真 ping 服务商（/models 端点）。"""
        url = (str(base_url) or "").rstrip("/") + "/models"
        try:
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + str(key or "")})
            with urllib.request.urlopen(req, timeout=8) as resp:
                codes = resp.getcode()
            return _ok(msg="连接成功（HTTP {}）".format(codes))
        except Exception as exc:
            return _err("连接失败：{}".format(getattr(exc, "reason", exc) or exc))

    # ---------- 配置读写 ----------
    def cfg_get(self):
        try:
            data = tomllib.loads(CFG_PATH.read_text(encoding="utf-8"))
            token = ((data.get("plastic_memory") or {}).get("dashboard_token"))
            if token:
                (data.get("plastic_memory") or {})["dashboard_token"] = token  # launcher 同机，不脱敏
            return _ok(data=data, path=str(CFG_PATH))
        except Exception as exc:
            return _err(exc)

    def cfg_file(self, name):
        """配置原文核对窗：bot/lpmm/adapter/env。"""
        mapping = {"bot": CFG_PATH, "lpmm": LPMM_PATH, "adapter": ADAPTER_CFG, "env": ENV_PATH}
        f = mapping.get(str(name))
        if not f:
            return _err("未知文件")
        try:
            return _ok(text=f.read_text(encoding="utf-8", errors="replace"))
        except FileNotFoundError:
            return _ok(text="（{} 还不存在）".format(f.name))
        except Exception as exc:
            return _err(exc)

    def cfg_save(self, payload):
        """payload: {ops:[{file:'bot'|'lpmm'|'adapter', section, key, value, kind}], restart:'auto'|'no'}
        白名单字段且 MaiCore 在线 → 走 K面板 /api/k/config/set（热生效）；
        其余文本手术写文件；有需重启的改动且 restart=auto → 停→写→拉起。"""
        try:
            ops = (payload or {}).get("ops") or []
            restart = (payload or {}).get("restart", "auto")
            if not ops:
                return _err("没有可保存的内容")
            hot_ops = [o for o in ops if (o.get("section"), o.get("key")) in HOT_FIELDS]
            cold_ops = [o for o in ops if (o.get("section"), o.get("key")) not in HOT_FIELDS]
            hot_n = 0
            bot_online = self.pm.state.get("bot") == "running"
            # 1) 热改：白名单字段走 K set（它自己写文件 + setattr）
            for o in hot_ops:
                if not bot_online:
                    continue
                kind = o.get("kind", "str")
                value = o.get("value")
                if kind == "bool":
                    coerced = value if isinstance(value, bool) else str(value).strip().lower() in ("true", "1", "是", "on", "y")
                elif kind == "int":
                    coerced = int(float(value))
                elif kind == "float":
                    coerced = float(value)
                else:
                    coerced = str(value).strip()
                r = _http("POST", "/api/k/config/set",
                          body={"section": o["section"], "key": o["key"], "value": coerced},
                          token=_dash_token())
                if r.get("ok"):
                    hot_n += 1
                else:
                    return _err("[{}.{}] 热改被拒：{}".format(o["section"], o["key"], r.get("error", "")))
            # 2) 冷改：文本手术（按文件分组，逐 op 写）；file='env' 走 .env 行替换
            files_changed = set()
            env_pairs = {}
            for o in cold_ops:
                if o.get("file") == "env":
                    literal, _v = _literal(o.get("kind", "str"), o.get("value"))
                    # .env 不要 TOML 引号：str/envstr 的 literal 是 "xxx" 带引号形式，json.loads 还原裸串
                    env_pairs[str(o["key"])] = json.loads(literal) if o.get("kind", "str") in ("str", "envstr") else literal
                    continue
                fname = o.get("file", "bot")
                path = {"bot": CFG_PATH, "lpmm": LPMM_PATH, "adapter": ADAPTER_CFG}.get(fname)
                if not path:
                    return _err("未知配置文件: " + str(fname))
                text = path.read_text(encoding="utf-8") if path.exists() else ""
                literal, _v = _literal(o.get("kind", "str"), o.get("value"))
                text = _set_key(text, o["section"], o["key"], literal)
                if tomllib is None:
                    return _err("缺少 tomllib（内嵌 Python 异常）")
                tomllib.loads(text)  # 写前校验
                path.write_text(text, encoding="utf-8")
                files_changed.add(fname)
            if env_pairs:
                self.env_set(env_pairs)
            # 3) 重启编排：有冷改 + 在线 + restart=auto
            # 真机实锤 2026-10-30：重启全家桶会把 NapCat/QQ 一起拉——频繁重启触发腾讯风控掉快速登录
            # （ErrCode 3 → 只能重新扫码）。配置保存只重启 MaiCore；动了适配器配置才连适配器一起重启，
            # NapCat 永不因配置保存重启（QQ 登录态太脆弱）。
            restarted = False
            if cold_ops and restart == "auto" and bot_online:
                self.log("配置保存 → 重启编排（仅 MaiCore" +
                         ("+适配器" if any(o.get("file") == "adapter" for o in cold_ops) else "") +
                         "；NapCat/QQ 不动，防掉登录）")
                self.pm.stop_service("bot")
                if any(o.get("file") == "adapter" for o in cold_ops):
                    self.pm.stop_service("adapter")
                    time.sleep(1.0)
                    self.pm.start_service("adapter")
                time.sleep(1.0)
                self.pm.start_service("bot")
                restarted = True
            msg = "已保存 {} 项".format(len(ops))
            if hot_n:
                msg += "（其中 {} 项立即生效）".format(hot_n)
            if restarted:
                msg += " · 已按需重启（约 30 秒后她回来），QQ 登录不受影响"
            elif cold_ops and bot_online:
                msg += " · 部分改动重启后生效"
            elif cold_ops:
                msg += " · 她下次启动时生效"
            return _ok(msg=msg, restarted=restarted)
        except ValueError as exc:
            return _err(str(exc))
        except Exception as exc:
            return _err("保存失败：{}".format(exc))

    def env_set(self, pairs):
        """pairs: {KEY: VALUE} —— .env 行替换（保留注释与顺序）。"""
        try:
            lines = ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True) if ENV_PATH.exists() else []
            for k, v in (pairs or {}).items():
                k = str(k).strip()
                hit = False
                for i, ln in enumerate(lines):
                    if ln.startswith(k + "="):
                        lines[i] = "{}={}\n".format(k, v)
                        hit = True
                        break
                if not hit:
                    if lines and not lines[-1].endswith("\n"):
                        lines[-1] += "\n"
                    lines.append("{}={}\n".format(k, v))
            ENV_PATH.write_text("".join(lines), encoding="utf-8")
            return _ok(msg=".env 已更新（{} 项）".format(len(pairs or {})))
        except Exception as exc:
            return _err(exc)

    # ---------- 人设 ----------
    def persona_get(self):
        try:
            data = tomllib.loads(CFG_PATH.read_text(encoding="utf-8"))
            bot = data.get("bot") or {}
            per = data.get("personality") or {}
            ide = data.get("identity") or {}
            return _ok(
                nickname=str(bot.get("nickname", "")),
                alias=", ".join(str(x) for x in (bot.get("alias_names") or [])),
                core=str(per.get("personality_core", "")),
                sides=[str(x) for x in (per.get("personality_sides") or [])],
                identity="；".join(str(x) for x in (ide.get("identity_detail") or [])),
                age=ide.get("age", ""),
                gender=str(ide.get("gender", "")),
                appearance=str(ide.get("appearance", "")),
            )
        except Exception as exc:
            return _err(exc)

    def persona_save(self, fields):
        """fields: {nickname, core, sides:[...], identity, age, gender, appearance} → bot_config 文本手术。"""
        try:
            text = CFG_PATH.read_text(encoding="utf-8")
            plan = [("bot", "nickname", "str", fields.get("nickname")),
                    ("personality", "personality_core", "str", fields.get("core")),
                    ("identity", "age", "int", fields.get("age")),
                    ("identity", "gender", "str", fields.get("gender")),
                    ("identity", "appearance", "str", fields.get("appearance"))]
            for sec, key, kind, val in plan:
                if val is None:
                    continue
                literal, _v = _literal(kind, val)
                text = _toml_set_scalar(text, sec, key, literal)
            sides = [str(x).strip() for x in (fields.get("sides") or []) if str(x).strip()]
            if sides:
                block = "\npersonality_sides = [\n" + "".join(
                    "    {},\n".format(json.dumps(x, ensure_ascii=False)) for x in sides) + "]"
                m = re.search(r"^personality_sides\s*=\s*\[[^\]]*\]", text, re.M | re.S)
                if m:
                    text = text[:m.start()] + block.lstrip("\n") + text[m.end():]
                else:
                    text = text.replace("[personality]", "[personality]\n" + block.strip(), 1)
            ident = str(fields.get("identity") or "").strip()
            if ident:
                items = [x.strip() for x in re.split(r"[;；\n]+", ident) if x.strip()]
                block = "\nidentity_detail = [\n" + "".join(
                    "    {},\n".format(json.dumps(x, ensure_ascii=False)) for x in items) + "]"
                m = re.search(r"^identity_detail\s*=\s*\[[^\]]*\]", text, re.M | re.S)
                if m:
                    text = text[:m.start()] + block.lstrip("\n") + text[m.end():]
                else:
                    text = text.replace("[identity]", "[identity]\n" + block.strip(), 1)
            tomllib.loads(text)
            CFG_PATH.write_text(text, encoding="utf-8")
            return _ok(msg="人设已写回 bot_config.toml（下轮对话生效）")
        except Exception as exc:
            return _err("人设保存失败：{}".format(exc))

    # ---------- 首启向导 ----------
    def wizard_save(self, qq, key, base_url="", ds_key="", style="tsundere", toggles=None):
        """向导真写入（config 未就绪 → 面板向导完成时调用）：
        [bot].qq + .env SILICONFLOW_KEY/BASE_URL + DEEP_SEEK_KEY + lpmm llm_providers 同步 + 人设底子。"""
        try:
            qq = re.sub(r"\D", "", str(qq))
            if not qq:
                return _err("QQ 号不能为空")
            # bot_config [bot].qq
            text = CFG_PATH.read_text(encoding="utf-8") if CFG_PATH.exists() else "[bot]\nqq = 0\n"
            literal, _v = _literal("int", qq)
            text = _toml_set_scalar(text, "bot", "qq", literal)
            # 人设底子（三选一）
            styles = {
                "tsundere": ("嘴硬心软，带一点傲娇的邻家女孩",
                             ["被夸会翘尾巴", "被戳痛处会炸毛", "嘴上说嫌弃，行动很诚实"]),
                "gentle": ("温柔细腻，擅长倾听的女孩",
                           ["说话软软的", "永远接得住情绪", "会记住你随口提的小事"]),
                "genki": ("元气满满的梗系女孩",
                          ["群里最吵的那个", "梗多话密", "活力四射，凌晨也不困"]),
            }
            core, sides = styles.get(str(style), styles["tsundere"])
            m = re.search(r"^personality_core\s*=\s*.*$", text, re.M)
            repl = "personality_core = " + json.dumps(core, ensure_ascii=False)
            text = text[:m.start()] + repl + text[m.end():] if m else text
            # toggles：表情包概率 / TTS / 主动闲逛(不用配) / 戳一戳(不用配)
            tg = toggles or {}
            if str(tg.get("emoji", "1")) in ("1", "true", "True"):
                pass  # 默认 emoji_chance 已有值
            m2 = re.search(r"^emoji_chance\s*=\s*.*$", text, re.M)
            if m2 and str(tg.get("emoji", "")) != "":
                text = text[:m2.start()] + "emoji_chance = 1" + text[m2.end():]  # 1 = 她自己看着办
            tomllib.loads(text)
            CFG_PATH.write_text(text, encoding="utf-8")
            # .env keys
            pairs = {}
            if str(key or "").strip():
                pairs["SILICONFLOW_KEY"] = str(key).strip()
                if str(base_url or "").strip():
                    pairs["SILICONFLOW_BASE_URL"] = str(base_url).strip()
            if str(ds_key or "").strip():
                pairs["DEEP_SEEK_KEY"] = str(ds_key).strip()
                pairs["DEEP_SEEK_BASE_URL"] = "https://api.deepseek.com/v1"
            if pairs:
                self.env_set(pairs)
            # lpmm llm_providers 同步（复刻 config_manager）
            try:
                if LPMM_PATH.exists():
                    ltext = LPMM_PATH.read_text(encoding="utf-8")
                    ldata = tomllib.loads(ltext)
                    provs = ldata.get("llm_providers") or []
                    hit = False
                    for p in provs:
                        if str(p.get("name", "")) == "siliconflow":
                            if pairs.get("SILICONFLOW_KEY"):
                                p["api_key"] = pairs["SILICONFLOW_KEY"]
                            if pairs.get("SILICONFLOW_BASE_URL"):
                                p["base_url"] = pairs["SILICONFLOW_BASE_URL"]
                            hit = True
                            break
                    if not hit and pairs.get("SILICONFLOW_KEY"):
                        # 追加一组 provider（文本追加，保注释）
                        block = "\n[[llm_providers]]\nname = \"siliconflow\"\nbase_url = \"{}\"\napi_key = \"{}\"\n".format(
                            pairs.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1/"), pairs["SILICONFLOW_KEY"])
                        ltext = ltext.rstrip("\n") + "\n" + block
                        LPMM_PATH.write_text(ltext, encoding="utf-8")
                    elif hit:
                        # 有命中时整体重写该文件（lpmm 注释少，可接受；无 tomli_w 用内嵌最小 dump）
                        LPMM_PATH.write_text(_mini_toml_dump(ldata), encoding="utf-8")
            except Exception as exc:
                self.log("lpmm 同步失败（不影响主流程）: {}".format(exc))
            return _ok(msg="配置已写入（QQ {} + Key 已落位）".format(qq))
        except Exception as exc:
            return _err("向导写入失败：{}".format(exc))

    def providers_save(self, cards):
        """服务商与密钥组：cards=[{name,url,key}]。卡0→SILICONFLOW 主通路，卡1→DEEP_SEEK 搜索通路。
        落点复刻 config_manager：.env 行替换 + lpmm_config [[llm_providers]] name=siliconflow 同步。"""
        try:
            pairs = {}
            if len(cards) > 0:
                c = cards[0] or {}
                if str(c.get("key", "")).strip():
                    pairs["SILICONFLOW_KEY"] = str(c["key"]).strip()
                if str(c.get("url", "")).strip():
                    pairs["SILICONFLOW_BASE_URL"] = str(c["url"]).strip()
            if len(cards) > 1:
                c = cards[1] or {}
                if str(c.get("key", "")).strip():
                    pairs["DEEP_SEEK_KEY"] = str(c["key"]).strip()
                    pairs.setdefault("DEEP_SEEK_BASE_URL", "https://api.deepseek.com/v1")
            if not pairs:
                return _err("没有需要保存的密钥（Key 留空则不改动）")
            r = self.env_set(pairs)
            if not r.get("ok"):
                return r
            # lpmm 同步（仅 siliconflow 卡有内容时）
            try:
                if LPMM_PATH.exists() and pairs.get("SILICONFLOW_KEY"):
                    ldata = tomllib.loads(LPMM_PATH.read_text(encoding="utf-8"))
                    provs = ldata.get("llm_providers") or []
                    hit = False
                    for p in provs:
                        if str(p.get("name", "")) == "siliconflow":
                            p["api_key"] = pairs["SILICONFLOW_KEY"]
                            if pairs.get("SILICONFLOW_BASE_URL"):
                                p["base_url"] = pairs["SILICONFLOW_BASE_URL"]
                            hit = True
                            break
                    if not hit:
                        provs.append({"name": "siliconflow",
                                      "base_url": pairs.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1/"),
                                      "api_key": pairs["SILICONFLOW_KEY"]})
                    ldata["llm_providers"] = provs
                    LPMM_PATH.write_text(_mini_toml_dump(ldata), encoding="utf-8")
            except Exception as exc:
                self.log("lpmm 密钥同步失败（不影响主流程）: {}".format(exc))
            return _ok(msg="密钥已保存（{} 项落位 · 重启后她的新钥匙生效）".format(len(pairs)))
        except Exception as exc:
            return _err("服务商保存失败：{}".format(exc))

    def day_stats(self):
        """总览四格：今日 事件流计数（收到/回复/拒发/新记忆）。"""
        try:
            from config_paths import EVENTS_FILE

            today = time.strftime("%m-%d")
            n = {"in": 0, "out": 0, "rej": 0, "mem": 0}
            if EVENTS_FILE.exists():
                size = EVENTS_FILE.stat().st_size
                with open(EVENTS_FILE, "r", encoding="utf-8", errors="ignore") as fh:
                    fh.seek(max(0, size - 1048576))  # 今日事件超 1MB 就近算（极端才丢）
                    block = fh.read()
                for ln in block.splitlines():
                    try:
                        ev = json.loads(ln)
                    except ValueError:
                        continue
                    if _ev_ts(ev.get("ts", 0)) and time.strftime(
                            "%m-%d", time.localtime(_ev_ts(ev.get("ts", 0)))) != today:
                        continue
                    node = str(ev.get("node", ""))
                    if node == "message_in":
                        n["in"] += 1
                    elif node == "send_out":
                        n["out"] += 1
                    elif node == "review" and not (ev.get("detail") or {}).get("passed", True):
                        n["rej"] += 1
                    elif node == "memory":
                        n["mem"] += 1
            return _ok(recv=n["in"], reply=n["out"], rejected=n["rej"], memory=n["mem"])
        except Exception as exc:
            return _err(exc)

    # ---------- QQ 扫码登录 ----------
    def qr_status(self):
        """NapCat 等扫码时返回二维码（读 NapCat 自己落的 cache/qrcode.png → base64）。
        need=False 表示无需扫码（进程不在 / 已连上 / 码过期）。"""
        try:
            from probes import napcat_process_alive, adapter_has_napcat_connection

            webui = "http://127.0.0.1:6099/webui?token=napcat"
            if not napcat_process_alive():
                return _ok(need=False)
            if adapter_has_napcat_connection():
                return _ok(need=False, logged_in=True)
            # 双保险：NapCat 日志出现登录成功信号也关弹窗（适配器连接偶有延迟/抖动）
            try:
                logs = sorted(NAPCAT_DIR.glob("versions/*/resources/app/napcat/logs/*.log"),
                              key=lambda p: p.stat().st_mtime)
                if logs:
                    tail = logs[-1].read_text(encoding="utf-8", errors="ignore")[-8192:]
                    if "登录成功" in tail or "快速登录" in tail:
                        return _ok(need=False, logged_in=True)
            except Exception:
                pass
            png, mt = None, 0.0
            if NAPCAT_DIR.exists():
                for c in NAPCAT_DIR.glob("versions/*/resources/app/napcat/cache/qrcode.png"):
                    try:
                        m = c.stat().st_mtime
                        if m > mt:
                            png, mt = c, m
                    except OSError:
                        continue
            if not png or time.time() - mt > 600:   # 10 分钟外的旧码不弹（QQ 码会过期）
                return _ok(need=True, qr="", url="", webui=webui,
                           note="二维码生成中…（首次登录约需 10-20 秒）")
            import base64

            b64 = base64.b64encode(png.read_bytes()).decode()
            # 解码 URL 从 NapCat 日志里顺带抓（面板做文字兜底）
            url = ""
            try:
                logs = sorted(NAPCAT_DIR.glob("versions/*/resources/app/napcat/logs/*.log"),
                              key=lambda p: p.stat().st_mtime)
                if logs:
                    tail = logs[-1].read_text(encoding="utf-8", errors="ignore")[-8192:]
                    m = re.search(r"二维码解码URL:\s*(\S+)", tail)
                    if m:
                        url = m.group(1)
            except Exception:
                pass
            return _ok(need=True, qr="data:image/png;base64," + b64, url=url, webui=webui, note="")
        except Exception as exc:
            return _err(exc)

    # ---------- 运行环境兜底（主程序拉不起来的两大历史根因） ----------
    def ensure_runnable(self):
        """启动 bot 前调用：
        1) EULA/隐私 更新后要求交互确认 → 后台启动没 stdin 直接 EOF 崩：launcher 代写 confirmed
           （安装墨忆即同意条款，与安装包许可协议页一致）。
        2) lpmm [[llm_providers]] 或 .env 的 key 全空 → knowledge_lib 模块级崩 Missing credentials：
           填占位值保她能上线（LLM 调用时 401，面板报错扫描可见），用户在向导/配置中心填真 key 即恢复。"""
        import hashlib

        notes = []
        try:
            for md, cf in (("EULA.md", "eula.confirmed"), ("PRIVACY.md", "privacy.confirmed")):
                f = MAIBOT_DIR / md
                c = MAIBOT_DIR / cf
                if not f.exists():
                    continue
                h = hashlib.md5(f.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
                if not (c.exists() and c.read_text(encoding="utf-8").strip() == h):
                    c.write_text(h, encoding="utf-8")
                    notes.append(md + " 已自动确认")
        except Exception as exc:
            notes.append("EULA 确认失败: {}".format(exc))
        try:
            if LPMM_PATH.exists():
                changed = False
                blocks = LPMM_PATH.read_text(encoding="utf-8").split("[[llm_providers]]")
                out = [blocks[0]]
                for b in blocks[1:]:
                    nm = re.search(r"name\s*=\s*\"([^\"]*)\"", b)
                    key = re.search(r"api_key\s*=\s*\"\"", b)
                    if nm and key and nm.group(1).strip().lower() != "localhost":
                        b = re.sub(r"api_key\s*=\s*\"\"", 'api_key = "sk-missing-please-set"', b)
                        changed = True
                    out.append(b)
                if changed:
                    LPMM_PATH.write_text("[[llm_providers]]".join(out), encoding="utf-8")
                    notes.append("检测到 API Key 未配置，已用占位值保她上线（LLM 会 401，去配置中心填真 Key）")
        except Exception as exc:
            notes.append("lpmm 兜底失败: {}".format(exc))
        try:
            if ENV_PATH.exists():
                env = ENV_PATH.read_text(encoding="utf-8")
                if re.search(r"^SILICONFLOW_KEY=\s*$", env, re.M):
                    env = re.sub(r"^SILICONFLOW_KEY=\s*$", "SILICONFLOW_KEY=sk-missing-please-set", env, flags=re.M)
                    ENV_PATH.write_text(env, encoding="utf-8")
                    notes.append(".env SILICONFLOW_KEY 未配置，已用占位值保她上线")
        except Exception as exc:
            notes.append(".env 兜底失败: {}".format(exc))
        for n in notes:
            self.log("[运行环境] " + n)
        return _ok(notes=notes)

    # ---------- 心流聚合 ----------
    def flow_status(self):
        """events.jsonl 尾部聚合：每条流的 interest 曲线 / 最后活跃 / 消息数 / 最近判定。"""
        try:
            from config_paths import EVENTS_FILE

            if not EVENTS_FILE.exists():
                return _ok(flows=[], main_mind="")
            size = EVENTS_FILE.stat().st_size
            with open(EVENTS_FILE, "r", encoding="utf-8", errors="ignore") as fh:
                fh.seek(max(0, size - 262144))
                block = fh.read()
            flows = {}
            order = []
            main_mind = ""
            for ln in block.splitlines():
                try:
                    ev = json.loads(ln)
                except ValueError:
                    continue
                sid = str(ev.get("stream_id", "") or "-")
                node = str(ev.get("node", ""))
                det = ev.get("detail") or {}
                f = flows.get(sid)
                if f is None:
                    f = flows[sid] = {"stream_id": sid, "k": 0.0, "hist": [], "msgs": 0,
                                      "last": 0.0, "willing": "", "mind": ""}
                    order.append(sid)
                if node == "message_in":
                    f["msgs"] += 1
                    t = det.get("time") or _ev_ts(ev.get("ts", 0)) or time.time()
                    try:
                        f["last"] = float(t)
                    except (TypeError, ValueError):
                        f["last"] = _ev_ts(t) or time.time()
                elif node == "willing":
                    try:
                        k = float(det.get("interest", det.get("k", 0)) or 0)
                    except (TypeError, ValueError):
                        k = 0.0
                    f["k"] = k
                    f["hist"].append(k)
                    if len(f["hist"]) > 40:
                        f["hist"] = f["hist"][-40:]
                    f["willing"] = "{} → {}（兴趣 {} / 门槛 {}）".format(
                        det.get("mode", "?"), det.get("decision", "?"), det.get("interest", "?"), det.get("threshold", "?"))
                elif node in ("think_start", "think_end") and det.get("thought"):
                    main_mind = str(det.get("thought"))[:120]
            out = []
            for sid in order:
                f = flows[sid]
                out.append({"stream_id": f["stream_id"], "k": round(f["k"], 2), "hist": f["hist"][-24:],
                            "msgs": f["msgs"], "last": f["last"], "willing": f["willing"]})
            out.sort(key=lambda x: -x["last"])
            return _ok(flows=out[:12], main_mind=main_mind)
        except Exception as exc:
            return _err(exc)


def _mini_toml_dump(data):
    """极小 TOML 序列化（仅 lpmm 同步用；字符串/数字/bool/一层表/数组）。"""
    def val(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return repr(v)
        if isinstance(v, list):
            return "[" + ", ".join(json.dumps(str(x) if not isinstance(x, (int, float, bool)) else x, ensure_ascii=False) for x in v) + "]"
        return json.dumps(str(v), ensure_ascii=False)
    lines = []
    for k, v in data.items():
        if isinstance(v, dict):
            lines.append("[{}]".format(k))
            for k2, v2 in v.items():
                if isinstance(v2, dict):
                    lines.append("[{}.{}]".format(k, k2))
                    for k3, v3 in v2.items():
                        lines.append("{} = {}".format(k3, val(v3)))
                elif isinstance(v2, list) and v2 and isinstance(v2[0], dict):
                    for item in v2:
                        lines.append("[[{}.{}]]".format(k, k2))
                        for k3, v3 in item.items():
                            lines.append("{} = {}".format(k3, val(v3)))
                else:
                    lines.append("{} = {}".format(k2, val(v2)))
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            for item in v:
                lines.append("[[{}]]".format(k))
                for k2, v2 in item.items():
                    lines.append("{} = {}".format(k2, val(v2)))
        else:
            lines.append("{} = {}".format(k, val(v)))
    return "\n".join(lines) + "\n"
