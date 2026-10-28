"""O10 冒烟：精准引用编号（冻结映射零漂移）。

断言直抄 src/plugins/utils/chat_message_builder.py 编号段（排序→跳过非数字id→#n前缀→映射），
与 read_mark 分段的 start_num=len(num_before)+1 接续语义。不 import 主链路重模块。
"""
from typing import Any, Dict, List


def apply_numbering(rows: List[Dict[str, Any]], start_num: int = 1):
    """直抄 _build_readable_messages_internal 编号段：按时间排序后编号，非数字id（notice等）不编号不进映射。"""
    ordered = sorted(rows, key=lambda r: r["time"])
    number_map: Dict[int, Dict[str, Any]] = {}
    for _offset, meta in enumerate(ordered):
        if not str(meta.get("message_id") or "").isdigit():
            continue  # 拍一拍等系统事件：照常展示但不编号、不进映射
        _n = start_num + _offset
        number_map[_n] = meta
        meta["content"] = f"#{_n} {meta.get('content', '')}"
    return ordered, number_map


def test_numbering_sorted_continuous():
    rows = [
        {"message_id": "300", "content": "第三条", "time": 103.0},
        {"message_id": "100", "content": "第一条", "time": 101.0},
        {"message_id": "200", "content": "第二条", "time": 102.0},
    ]
    ordered, num_map = apply_numbering(rows)
    assert [r["content"] for r in ordered] == ["#1 第一条", "#2 第二条", "#3 第三条"]
    assert sorted(num_map.keys()) == [1, 2, 3]
    assert num_map[2]["message_id"] == "200"  # 编号→真实消息映射冻结


def test_notice_not_numbered():
    rows = [
        {"message_id": "notice", "content": "某某 拍了拍 你", "time": 101.0},
        {"message_id": "201", "content": "哈哈", "time": 102.0},
    ]
    ordered, num_map = apply_numbering(rows)
    # notice 照常展示但无 #n 前缀、不进映射
    assert ordered[0]["content"] == "某某 拍了拍 你"
    assert 1 not in num_map
    # 冻结现场行为：编号按全列表 offset 递增（notice 占位但不出号，出现号位间隙）
    assert ordered[1]["content"] == "#2 哈哈"
    assert sorted(num_map.keys()) == [2]


def test_read_mark_continuation():
    """read_mark 分段：未读段 start_num=len(num_before)+1，全窗口编号连续。"""
    before_rows = [
        {"message_id": "101", "content": "已读一", "time": 101.0},
        {"message_id": "102", "content": "已读二", "time": 102.0},
    ]
    _, num_before = apply_numbering([dict(r) for r in before_rows])
    assert sorted(num_before.keys()) == [1, 2]

    after_rows = [
        {"message_id": "201", "content": "未读一", "time": 103.0},
        {"message_id": "202", "content": "未读二", "time": 104.0},
    ]
    ordered_after, num_after = apply_numbering([dict(r) for r in after_rows], start_num=len(num_before) + 1)
    assert ordered_after[0]["content"] == "#3 未读一"
    assert ordered_after[1]["content"] == "#4 未读二"

    merged = {**num_before, **num_after}
    assert sorted(merged.keys()) == [1, 2, 3, 4]  # 合并映射无断号


def test_read_mark_continuation_with_trailing_notice():
    # 已读段末尾是 notice：num_before 只数数字id行，跨段仍连续（现场 start_num=len(num_before)+1 语义）
    _, num_before = apply_numbering(
        [
            {"message_id": "101", "content": "a", "time": 101.0},
            {"message_id": "notice", "content": "拍了拍", "time": 102.0},
        ]
    )
    assert sorted(num_before.keys()) == [1]
    ordered_after, _ = apply_numbering(
        [{"message_id": "301", "content": "b", "time": 103.0}], start_num=len(num_before) + 1
    )
    assert ordered_after[0]["content"] == "#2 b"
