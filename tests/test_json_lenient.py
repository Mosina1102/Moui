"""O10 冒烟：宽松 JSON 提取（_lenient_json_extract）——10-27 实录验尸固化。

事故实录：模型按教学合法填 at_qq（字段顺序 reply→at_qq→emoji_query），
旧提取正则硬编码"reply 后跟 emoji_query"落空 → fallback 贪婪回溯把 `", "at_qq": "`
吞进 reply，`饭慢慢咽", "at_qq": "` 原样发进群里穿帮。
逻辑直抄 src/plugins/heartFC_chat/unified_brain.py _lenient_json_extract（v2.4 修复版）。
不 import 主链路重模块。
"""
import re


def lenient_extract(content: str):
    """直抄 v2.4 修复版宽松提取核心逻辑（含泄漏体检保险丝）。"""
    logger_warns = []

    start, end = content.find("{"), content.rfind("}")
    if not (0 <= start < end):
        return None
    obj = content[start : end + 1]
    out = {}
    m = re.search(r'"action"\s*:\s*"([a-zA-Z_]+)"', obj)
    if not m:
        return None
    out["action"] = m.group(1)
    m = re.search(r'"reasoning"\s*:\s*"([\s\S]*)"\s*(?=,\s*"[a-z_]+"|\})', obj)
    out["reasoning"] = m.group(1) if m else ""
    reply = ""
    m = re.search(r'"reply"\s*:\s*"([\s\S]*)"\s*(?=,\s*"at_qq"|,\s*"emoji_query"|,\s*"[a-z_]+"|\})', obj)
    if m:
        reply = m.group(1)
    _leak = re.search(r'"\s*,\s*"(?:at_qq|emoji_query|action|reasoning|affection_update)"\s*:', reply)
    if _leak:
        logger_warns.append(reply[-40:])
        reply = reply[: _leak.start()]
    out["reply"] = reply
    m = re.search(r'"emoji_query"\s*:\s*"([^"]*)"', obj)
    out["emoji_query"] = m.group(1) if m else ""
    m = re.search(r'"at_qq"\s*:\s*"?(\d+)', obj)
    out["at_qq"] = m.group(1) if m else ""
    return out


# 10-27 真实事故样本（reply→at_qq→emoji_query 字段顺序）
ACCIDENT_SAMPLE = (
    '{"action": "text_reply", "reasoning": "点你名的先举手", '
    '"reply": "[引用]点你名的 先举手算到 饭慢慢咽", "at_qq": "3808607562", "emoji_query": ""}'
)

# 无 at_qq 的常规顺序
PLAIN_SAMPLE = '{"action": "text_reply", "reasoning": "接梗", "reply": "行吧", "emoji_query": ""}'

# 值内未转义引号（v2.2 补丁的原始场景）
QUOTE_IN_VALUE = '{"action": "text_reply", "reasoning": "他说"笑死了"", "reply": "是挺搞笑的", "emoji_query": ""}'


def test_accident_sample_no_json_leak():
    data = lenient_extract(ACCIDENT_SAMPLE)
    assert data["action"] == "text_reply"
    assert data["reply"] == "[引用]点你名的 先举手算到 饭慢慢咽"
    assert "at_qq" not in data["reply"]
    assert data["at_qq"] == "3808607562"


def test_plain_order_reply():
    data = lenient_extract(PLAIN_SAMPLE)
    assert data["reply"] == "行吧"
    assert data["at_qq"] == ""


def test_quote_in_value_kept():
    # 值内未转义引号不能把 reply 截断到只剩半句（v2.2 场景保持兼容）
    data = lenient_extract(QUOTE_IN_VALUE)
    assert "笑死了" in data["reasoning"]
    assert data["reply"] == "是挺搞笑的"


def test_leak_fuse_truncates():
    # 保险丝：即使贪婪回溯把字段残迹吞进 reply，体检也会截断到残迹前
    dirty = '{"action": "text_reply", "reply": "你好", "at_qq": "1"}'
    data = lenient_extract(dirty)
    assert data["reply"] == "你好"
    assert data["at_qq"] == "1"


def test_action_missing_returns_none():
    assert lenient_extract("完全不是JSON的文本") is None
    assert lenient_extract('{"no_action_field": 1}') is None
