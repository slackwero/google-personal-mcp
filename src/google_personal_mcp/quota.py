"""Client-side token bucket that keeps us under one API's per-user quota."""

import threading
import time
from collections.abc import Callable

# One metadata batch: 50 messages at 20 units each.
DEFAULT_CAPACITY = 1000
DEFAULT_UNITS_PER_MINUTE = 5000


class QuotaLimiter:
    """Thread-safe token bucket.

    The bucket holds at most `capacity` units and refills continuously at `units_per_minute / 60` units per second,
    so any 60-second window spends at most `capacity + units_per_minute` units. The defaults (1,000 + 5,000) keep
    that at Gmail's 6,000 units per user per minute; Calendar passes its own capacity and
    rate. Callers are served one at a time: a caller that has to wait sleeps while holding the lock, so concurrent
    callers can never overdraw the bucket.
    """

    def __init__(
        self,
        units_per_minute: int = DEFAULT_UNITS_PER_MINUTE,
        *,
        capacity: int = DEFAULT_CAPACITY,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._capacity = capacity
        self._rate = units_per_minute / 60
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._available = float(capacity)
        self._last = clock()

    def acquire(self, units: int) -> None:
        """Block until `units` are available, then consume them."""
        if units <= 0:
            raise ValueError(f"Requested units must be positive; got {units}")
        if units > self._capacity:
            raise ValueError(f"Requested {units} units exceeds the capacity of {self._capacity}")
        with self._lock:
            self._refill()
            if self._available < units:
                self._sleep((units - self._available) / self._rate)
                self._refill()
            self._available -= units

    def _refill(self) -> None:
        now = self._clock()
        self._available = min(self._capacity, self._available + (now - self._last) * self._rate)
        self._last = now
