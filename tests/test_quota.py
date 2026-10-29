import threading

import pytest

from google_personal_mcp.quota import QuotaLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_burst_up_to_capacity_does_not_sleep():
    clock, sleeps = FakeClock(), []
    q = QuotaLimiter(600, capacity=600, clock=clock, sleep=sleeps.append)
    q.acquire(600)
    assert sleeps == []


def test_waits_for_refill():
    clock, sleeps = FakeClock(), []
    q = QuotaLimiter(600, capacity=600, clock=clock, sleep=lambda s: (sleeps.append(s), clock.advance(s)))
    q.acquire(600)
    q.acquire(20)
    assert sleeps == [pytest.approx(2.0)]  # 600/min -> 10 units/s


def test_refills_with_time_but_never_above_capacity():
    clock, sleeps = FakeClock(), []
    q = QuotaLimiter(600, capacity=600, clock=clock, sleep=sleeps.append)
    q.acquire(600)
    clock.advance(3600)
    q.acquire(600)
    assert sleeps == []


def test_rejects_more_than_capacity():
    with pytest.raises(ValueError, match="exceeds"):
        QuotaLimiter(capacity=100).acquire(101)


@pytest.mark.parametrize("units", [0, -1])
def test_rejects_non_positive_units(units):
    with pytest.raises(ValueError, match="positive"):
        QuotaLimiter().acquire(units)


def test_default_capacity_is_one_metadata_batch():
    clock, sleeps = FakeClock(), []
    q = QuotaLimiter(clock=clock, sleep=sleeps.append)
    q.acquire(1000)
    assert sleeps == []
    with pytest.raises(ValueError, match="exceeds the capacity of 1000"):
        q.acquire(1001)


def test_defaults_keep_every_minute_at_or_below_the_per_user_quota():
    clock = FakeClock()
    q = QuotaLimiter(clock=clock, sleep=clock.advance)
    times: list[float] = []
    while clock.now <= 180:
        q.acquire(1000)
        times.append(clock.now)
    for start in times:
        in_window = [t for t in times if start <= t <= start + 60 + 1e-9]
        assert 1000 * len(in_window) <= 6000, start


def test_waiting_callers_are_served_one_at_a_time():
    clock = FakeClock()
    second_caller: list[threading.Thread] = []
    blocked_while_first_slept: list[bool] = []

    def sleep(seconds: float) -> None:
        if threading.current_thread() is threading.main_thread():
            second_caller.append(threading.Thread(target=q.acquire, args=(600,)))
            second_caller[0].start()
            second_caller[0].join(timeout=0.2)
            blocked_while_first_slept.append(second_caller[0].is_alive())
        clock.advance(seconds)

    q = QuotaLimiter(600, capacity=600, clock=clock, sleep=sleep)
    q.acquire(600)
    q.acquire(600)  # empty bucket: sleeps 60 s, and meanwhile a second caller also asks for 600
    second_caller[0].join(timeout=5)
    assert blocked_while_first_slept == [True]
    assert clock.now == pytest.approx(120.0)  # the second caller waited for its own refill
