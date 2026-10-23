"""Small building blocks for calling other Agrolinking services without letting them take us down."""

import contextvars
import threading
import time
from collections import deque
from typing import Callable, Optional

# The id of the request being handled, so a call to another service can pass it
# on and a problem can be followed across the logs of both services. Set by
# whatever starts the work (a request handler or a job); None when there is none.
request_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)


class CircuitBreaker:
    """
    Stops calling a service that keeps failing, so a down service costs us one
    fast refusal instead of a slow timeout on every request.

    Closed (normal) until `failure_threshold` calls fail within `window_seconds`.
    Then open: every call is refused for `cooldown_seconds`. After that one trial
    call is let through: if it works the breaker closes, if it fails it opens again.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        window_seconds: float = 60.0,
        cooldown_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: deque[float] = deque()
        self._opened_at: Optional[float] = None
        self._trial_in_flight = False

    @property
    def state(self) -> str:
        with self._lock:
            if self._opened_at is None:
                return "closed"
            if self._clock() - self._opened_at >= self.cooldown_seconds:
                return "half-open"
            return "open"

    def allow(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return True
            if self._clock() - self._opened_at < self.cooldown_seconds:
                return False
            if self._trial_in_flight:
                return False
            self._trial_in_flight = True
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures.clear()
            self._opened_at = None
            self._trial_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            now = self._clock()
            self._trial_in_flight = False
            if self._opened_at is not None:
                self._opened_at = now
                return
            self._failures.append(now)
            while self._failures and now - self._failures[0] > self.window_seconds:
                self._failures.popleft()
            if len(self._failures) >= self.failure_threshold:
                self._opened_at = now
