import threading
import time
from collections.abc import Callable


class Throttle:
    def __init__(
        self,
        rate_for: Callable[[str], float],
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._rate_for = rate_for
        self._clock = clock
        self._sleep = sleep
        self._next_slot: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, domain: str) -> float:
        interval = 1.0 / self._rate_for(domain)
        with self._lock:
            now = self._clock()
            slot = max(now, self._next_slot.get(domain, now))
            self._next_slot[domain] = slot + interval
        delay = slot - now
        if delay > 0:
            self._sleep(delay)
        return max(delay, 0.0)
