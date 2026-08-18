"""限流器测试：plan 测试清单 7、8、9。"""

from kapibala.rate_limiter import SlidingWindowRateLimiter


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def make_limiter():
    clock = FakeClock()
    return SlidingWindowRateLimiter(window_seconds=60, max_per_window=1, clock=clock), clock


def test_first_send_allowed_second_within_window_rejected():
    limiter, _ = make_limiter()
    assert limiter.allow("c1")
    limiter.record("c1")
    assert not limiter.allow("c1")


def test_boundary_59_seconds_still_blocked():
    limiter, clock = make_limiter()
    limiter.record("c1")
    clock.advance(59)
    assert not limiter.allow("c1")


def test_boundary_60_seconds_allowed():
    """恰好 60 秒：上一条已滑出窗口（窗口为左闭右开 [t-60, t)）。"""
    limiter, clock = make_limiter()
    limiter.record("c1")
    clock.advance(60)
    assert limiter.allow("c1")


def test_boundary_61_seconds_allowed():
    limiter, clock = make_limiter()
    limiter.record("c1")
    clock.advance(61)
    assert limiter.allow("c1")


def test_allow_is_read_only_and_does_not_consume_quota():
    """LLM 重试/策略重算只调 allow()，不占发送配额（清单 9）。"""
    limiter, _ = make_limiter()
    for _ in range(10):
        assert limiter.allow("c1")  # 反复检查不记录
    limiter.record("c1")
    assert not limiter.allow("c1")


def test_sliding_window_not_fixed_minute():
    """滑动窗口：与"每分钟固定窗口"不同，按真实发送时刻计算。"""
    limiter, clock = make_limiter()
    limiter.record("c1")
    clock.advance(30)
    limiter.record("c2")  # 另一客户不受限
    assert not limiter.allow("c1")
    clock.advance(29)  # t=59，c1 仍被限
    assert not limiter.allow("c1")
    clock.advance(1)  # t=60，c1 解禁
    assert limiter.allow("c1")
    assert not limiter.allow("c2")  # c2 的 60 秒还没到


def test_customers_are_isolated():
    limiter, _ = make_limiter()
    limiter.record("c1")
    assert limiter.allow("c2")
