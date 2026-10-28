"""O10 冒烟：引用防脏——消息 id 纯数字校验（六层加固的统一判据）。

背景验尸：拍一拍 notice 等非数字消息id会让 QQ 适配器挂出无效引用框/乱码。
判据直抄现场各层（chat_message_builder 编号段 / outbound_enhance 锚点 / message.py 挂载）：
    str(message_id or "").isdigit()
不 import 主链路重模块。
"""


def quotable(message_id) -> bool:
    """直抄现场统一判据：QQ真实消息id必为纯数字；非数字一律降级普通消息。"""
    return str(message_id or "").isdigit()


def test_reject_notice_ids():
    assert not quotable("notice")  # 拍一拍/戳一拍系统事件
    assert not quotable("mid_pf_123")  # 占位id
    assert not quotable("")  # 空id
    assert not quotable(None)
    assert not quotable("123abc")
    assert not quotable(" 123")  # 带空格
    assert not quotable("123 ")


def test_accept_real_ids():
    assert quotable("123456789")
    assert quotable(123456789)  # int 型消息id（部分链路未转str）
    assert quotable("0")


def test_guard_in_numbering_semantics():
    # 编号段行为：非数字id → 不编号不进映射（跳过，不抛错）
    rows = [
        {"message_id": "notice", "content": "拍了拍", "time": 1.0},
        {"message_id": "555", "content": "正常", "time": 2.0},
        {"message_id": "", "content": "脏id", "time": 3.0},
    ]
    num_map = {}
    for offset, meta in enumerate(sorted(rows, key=lambda r: r["time"])):
        if not quotable(meta["message_id"]):
            continue
        num_map[offset + 1] = meta
    assert sorted(num_map.keys()) == [2]  # 只有 555 进了映射
    assert num_map[2]["message_id"] == "555"
