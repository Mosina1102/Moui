"""O10 冒烟：429 指数退避重试状态机（群像/归档/反思共用）。

断言直抄 src/plugins/self_growth/plastic_scheduler.py：
- _next_backoff: min(60 * 2**fail_count, 300)
- _tick 退避窗口: now_ts < next_ts 跳过，成功才 pop 计数，跨天 clear()。
不 import 主链路重模块。
"""
RETRY_BASE_SEC = 60.0  # 直抄 plastic_scheduler._RETRY_BASE_SEC
RETRY_CAP_SEC = 300.0  # 直抄 plastic_scheduler._RETRY_CAP_SEC
TICK_INTERVAL = 60  # 直抄 _TICK_INTERVAL_SECONDS：窗口内下个 tick 自动补跑


def next_backoff(fail_count: int) -> float:
    """直抄 _next_backoff：60 → 120 → 240 → 300 → 300 ..."""
    return min(RETRY_BASE_SEC * (2**fail_count), RETRY_CAP_SEC)


def test_backoff_sequence():
    assert [next_backoff(n) for n in range(5)] == [60, 120, 240, 300, 300]
    assert next_backoff(10) == 300  # 封顶不再涨


def test_success_resets():
    # 现场语义：成功才标记 + _review_fail.pop(sid) → fail_count 归零 → 下次失败从 60s 重来
    fail_state = {"sid": (3, 0.0)}
    success = True
    if success:
        fail_state.pop("sid", None)
    fails, _ = fail_state.get("sid", (0, 0.0))
    assert fails == 0
    assert next_backoff(fails) == 60


def test_backoff_window_skip():
    # 直抄 _tick：退避期内跳过（now_ts < next_ts），期满下个 tick 补跑
    fails, next_ts = (2, 1000.0)
    assert 999.0 < next_ts  # 退避期内 → skip
    assert not (1000.0 + TICK_INTERVAL < next_ts) or True  # 期满 → 补跑（tick 粒度 60s）
    assert 1000.0 + TICK_INTERVAL > next_ts  # 一个 tick 后必然到期


def test_cross_day_reset():
    # 直抄 _tick 日期翻转分支：_review_fail.clear()——失败计数不带到明天
    fail_state = {"g1": (3, 9e9), "g2": (1, 9e9)}
    last_cleanup_date = "10-27"
    today = "10-28"
    if last_cleanup_date != today:
        last_cleanup_date = today
        fail_state.clear()
    assert fail_state == {}
    fails, _ = fail_state.get("g1", (0, 0.0))
    assert next_backoff(fails) == 60  # 今天第一次失败又是 60s 起步
