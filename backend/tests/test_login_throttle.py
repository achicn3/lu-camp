"""登入節流（LoginThrottle）單元測試：假時鐘、視窗、門檻、成功重置。"""

from app.modules.user.throttle import LoginThrottle, ThrottlePolicy


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _throttle(clock: FakeClock, **overrides: float | int) -> LoginThrottle:
    policy = ThrottlePolicy(
        window_seconds=float(overrides.get("window_seconds", 900.0)),
        max_failures_per_username=int(overrides.get("max_failures_per_username", 5)),
        max_failures_per_ip=int(overrides.get("max_failures_per_ip", 20)),
        max_tracked_buckets=int(overrides.get("max_tracked_buckets", 10_000)),
    )
    return LoginThrottle(policy=policy, clock=clock)


def test_allows_until_username_threshold() -> None:
    clock = FakeClock()
    throttle = _throttle(clock)
    for _ in range(4):
        throttle.record_failure("alice", "10.0.0.1")
    assert throttle.retry_after("alice", "10.0.0.1") is None  # 第 5 次嘗試仍可
    throttle.record_failure("alice", "10.0.0.1")
    retry = throttle.retry_after("alice", "10.0.0.1")
    assert retry is not None and retry > 0  # 達 5 次失敗 → 鎖


def test_other_username_unaffected() -> None:
    clock = FakeClock()
    throttle = _throttle(clock)
    for _ in range(5):
        throttle.record_failure("alice", "10.0.0.1")
    assert throttle.retry_after("bob", "10.0.0.1") is None


def test_window_expiry_unlocks() -> None:
    clock = FakeClock()
    throttle = _throttle(clock, window_seconds=900.0)
    for _ in range(5):
        throttle.record_failure("alice", "10.0.0.1")
    assert throttle.retry_after("alice", "10.0.0.1") is not None
    clock.now += 901.0
    assert throttle.retry_after("alice", "10.0.0.1") is None


def test_success_resets_username_but_not_ip() -> None:
    clock = FakeClock()
    throttle = _throttle(clock, max_failures_per_ip=6)
    for _ in range(4):
        throttle.record_failure("alice", "10.0.0.1")
    throttle.record_success("alice", "10.0.0.1")
    assert throttle.retry_after("alice", "10.0.0.1") is None
    # IP 計數不因單一帳號成功而歸零（仍防同 IP 噴灑多帳號）
    throttle.record_failure("bob", "10.0.0.1")
    throttle.record_failure("carol", "10.0.0.1")
    assert throttle.retry_after("dave", "10.0.0.1") is not None  # 4+2 ≥ 6 → IP 鎖


def test_ip_threshold_across_usernames() -> None:
    clock = FakeClock()
    throttle = _throttle(clock, max_failures_per_ip=20)
    for i in range(20):
        throttle.record_failure(f"user-{i}", "10.0.0.9")
    assert throttle.retry_after("fresh-user", "10.0.0.9") is not None
    assert throttle.retry_after("fresh-user", "10.0.0.8") is None  # 其他 IP 不受影響


def test_read_path_does_not_allocate_state() -> None:
    """retry_after 對未知帳號/IP 不建任何桶（防攻擊者以無限 username 撐爆記憶體）。"""
    clock = FakeClock()
    throttle = _throttle(clock)
    for i in range(100):
        assert throttle.retry_after(f"ghost-{i}", f"10.0.{i}.1") is None
    assert throttle.tracked_buckets() == 0


def test_expired_buckets_are_dropped_on_touch() -> None:
    clock = FakeClock()
    throttle = _throttle(clock, window_seconds=900.0)
    throttle.record_failure("alice", "10.0.0.1")
    assert throttle.tracked_buckets() > 0
    clock.now += 901.0
    assert throttle.retry_after("alice", "10.0.0.1") is None
    assert throttle.tracked_buckets() == 0  # 過期桶在觸碰時即刪除


def test_cardinality_is_bounded_under_username_flood() -> None:
    """以海量唯一 username 灌失敗：追蹤桶數不得超過上限（記憶體有界）。"""
    clock = FakeClock()
    throttle = _throttle(clock, max_tracked_buckets=50)
    for i in range(500):
        throttle.record_failure(f"flood-{i}", "10.0.0.1")
    assert throttle.tracked_buckets() <= 50 + 1  # username 桶受限（+1 為同一 IP 桶）


def test_eviction_keeps_throttle_functional() -> None:
    """逐出舊桶後，新的失敗仍正常累計與鎖定。"""
    clock = FakeClock()
    throttle = _throttle(clock, max_tracked_buckets=10)
    for i in range(100):
        throttle.record_failure(f"flood-{i}", "10.0.0.1")
    for _ in range(5):
        throttle.record_failure("target", "10.0.0.2")
    assert throttle.retry_after("target", "10.0.0.2") is not None


def test_retry_after_counts_down_with_time() -> None:
    clock = FakeClock()
    throttle = _throttle(clock, window_seconds=900.0)
    for _ in range(5):
        throttle.record_failure("alice", "10.0.0.1")
    first = throttle.retry_after("alice", "10.0.0.1")
    clock.now += 300.0
    later = throttle.retry_after("alice", "10.0.0.1")
    assert first is not None and later is not None
    assert later < first
