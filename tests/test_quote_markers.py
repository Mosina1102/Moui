"""O10 冒烟：引用标记家族（[引用]/[引用自己]/[引用图]/[引用#n]）。

断言直抄现场正则（改源码时同步改这里）：
- QUOTE_MARKER_RE / 括号清理正则 ← src/plugins/chat/utils.py
- STALE_QUOTE_MARKER_RE / SELF_QUOTE_MARKER / RESPONSE_QUOTE_MARKER ← src/plugins/chat/outbound_enhance.py
不 import 主链路重模块（零依赖，纯正则固化验尸断言）。
"""
import re

# ---- 直抄：chat/utils.py ----
QUOTE_MARKER_RE = re.compile(r"\[引用(?:自己|图|#\d+)?\]")
# 括号清理：含中文的括号内容删除，引用功能标记豁免（不能当括号内容删掉）
BRACKET_STRIP_RE = re.compile(r"[\(\[\（](?!(?:引用(?:自己|图|#\d+)?\]))(?=.*[\u4e00-\u9fff]).*?[\)\]\）]")

# ---- 直抄：chat/outbound_enhance.py ----
SELF_QUOTE_MARKER = "[引用自己]"
RESPONSE_QUOTE_MARKER = "[引用]"
# 本链路不解析的标记（[引用图]已废弃 / [引用#n]只在HeartFC解析）→ 静默剥除防泄漏
STALE_QUOTE_MARKER_RE = re.compile(r"\[引用(?:图|#\d+)\]")


def test_quote_marker_family_fullmatch():
    assert QUOTE_MARKER_RE.fullmatch("[引用]")
    assert QUOTE_MARKER_RE.fullmatch("[引用自己]")
    assert QUOTE_MARKER_RE.fullmatch("[引用图]")
    assert QUOTE_MARKER_RE.fullmatch("[引用#12]")
    assert not QUOTE_MARKER_RE.fullmatch("[引用啊]")
    assert not QUOTE_MARKER_RE.fullmatch("[引用#]")
    assert not QUOTE_MARKER_RE.fullmatch("[引用#abc]")


def test_quote_marker_search_in_sentence():
    assert QUOTE_MARKER_RE.search("等一下[引用#3]这个说得对")
    assert QUOTE_MARKER_RE.search("[引用自己] 我刚说的")
    assert not QUOTE_MARKER_RE.search("普通消息没有标记")


def test_bracket_strip_keeps_markers():
    # 含中文括号内容被删，但引用标记必须原样保留（验尸点：标记被括号清理吃掉=引用失效）
    assert BRACKET_STRIP_RE.sub("", "（笑）你好[引用#2]") == "你好[引用#2]"
    assert BRACKET_STRIP_RE.sub("", "(小声)[引用]在吗") == "[引用]在吗"
    assert BRACKET_STRIP_RE.sub("", "[引用自己]（吓我一跳）") == "[引用自己]"


def test_stale_marker_strip():
    assert STALE_QUOTE_MARKER_RE.sub("", "看看[引用图]") == "看看"
    assert STALE_QUOTE_MARKER_RE.sub("", "接[引用#7]着说") == "接着说"
    # [引用] / [引用自己] 不是 stale 标记，不能被剥
    assert not STALE_QUOTE_MARKER_RE.search("拿[引用]说事")
    assert not STALE_QUOTE_MARKER_RE.search(SELF_QUOTE_MARKER)
    assert RESPONSE_QUOTE_MARKER == "[引用]"


def test_marker_not_double_stripped_by_marker_re():
    # 括号清理后仍要能被标记家族识别（两道工序兼容）
    cleaned = BRACKET_STRIP_RE.sub("", "（想想）[引用#9]对")
    assert QUOTE_MARKER_RE.search(cleaned)
    assert QUOTE_MARKER_RE.fullmatch("[引用#9]")
