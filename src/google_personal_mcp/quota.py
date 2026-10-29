"""Client-side token bucket that keeps us under the Gmail per-user quota."""

import time
from collections.abc import Callable


class QuotaLimiter:
    """Token bucket: capacity `units_per_minute`, refilled continuously at `units_per_minute / 60` per second."""

    def __init__(
        self,
        units_per_minute: int = 5000,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._capacity = units_per_minute
        self._rate = units_per_minute / 60
        self._clock = clock
        self._sleep = sleep
        self._available = float(units_per_minute)
        self._last = clock()

    def acquire(self, units: int) -> None:
        """Block until `units` are available, then consume them."""
        if units > self._capacity:
            raise ValueError(f"Requested {units} units exceeds the capacity of {self._capacity}")
        self._refill()
        if self._available < units:
            self._sleep((units - self._available) / self._rate)
            self._refill()
        self._available -= units

    def _refill(self) -> None:
        now = self._clock()
        self._available = min(self._capacity, self._available + (now - self._last) * self._rate)
        self._last = now
