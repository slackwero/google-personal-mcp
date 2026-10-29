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
    q = QuotaLimiter(600, clock=clock, sleep=sleeps.append)
    q.acquire(600)
    assert sleeps == []


def test_waits_for_refill():
    clock, sleeps = FakeClock(), []
    q = QuotaLimiter(600, clock=clock, sleep=lambda s: (sleeps.append(s), clock.advance(s)))
    q.acquire(600)
    q.acquire(20)
    assert sleeps == [pytest.approx(2.0)]  # 600/min -> 10 units/s


def test_refills_with_time_but_never_above_capacity():
    clock, sleeps = FakeClock(), []
    q = QuotaLimiter(600, clock=clock, sleep=sleeps.append)
    q.acquire(600)
    clock.advance(3600)
    q.acquire(600)
    assert sleeps == []


def test_rejects_more_than_capacity():
    with pytest.raises(ValueError, match="exceeds"):
        QuotaLimiter(100).acquire(101)
