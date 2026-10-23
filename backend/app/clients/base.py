"""
A careful HTTP reader for another Agrolinking service's JSON reference data.

What it does for every call:
  * short timeouts (2 s to connect, 5 s to read) and two retries with backoff,
    on timeouts, connection errors and 5xx only. A 4xx is a real answer, not
    retried.
  * a circuit breaker, so a service that is down is refused instantly.
  * a copy of the last good answer, kept for the cache lifetime and re-checked
    with If-None-Match, so an unchanged answer costs an empty 304.
  * if the service is unreachable, the last good copy is served and marked
    stale. If there is none, the caller gets an explicit "unavailable" result
    with a reason. It never raises, so one broken helper cannot take a page down.
  * a response that does not match its contract is logged as a contract error
    and never passed on or cached.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Generic, Optional, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.clients.resilience import CircuitBreaker, request_id

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

DEFAULT_TIMEOUT = httpx.Timeout(5.0, connect=2.0)


@dataclass
class ClientResult(Generic[T]):
    """Either a value, or the reason there isn't one. Never an exception."""

    value: Optional[T] = None
    unavailable_reason: Optional[str] = None
    stale: bool = False

    @property
    def ok(self) -> bool:
        return self.value is not None


@dataclass
class _CacheEntry:
    value: BaseModel
    etag: Optional[str]
    fetched_at: float = field(default=0.0)


class ServiceClient:
    def __init__(
        self,
        name: str,
        base_url: str,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        retries: int = 2,
        backoff_seconds: float = 0.25,
        breaker: Optional[CircuitBreaker] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        headers: Optional[dict[str, str]] = None,
    ):
        self.name = name
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self.breaker = breaker or CircuitBreaker(clock=clock)
        self._clock = clock
        self._sleep = sleep
        self._cache: dict[tuple[str, type], _CacheEntry] = {}
        self._lock = threading.Lock()
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport, headers=headers or {}
        )

    def close(self) -> None:
        self._http.close()

    def fetch(self, path: str, model: type[T], ttl_seconds: float) -> ClientResult[T]:
        key = (path, model)
        with self._lock:
            cached = self._cache.get(key)
        now = self._clock()

        if cached is not None and now - cached.fetched_at < ttl_seconds:
            return ClientResult(value=cached.value)  # type: ignore[arg-type]

        if not self.breaker.allow():
            return self._fallback(cached, f"{self.name} is not responding (circuit open)")

        headers = {}
        rid = request_id.get()
        if rid:
            headers["X-Request-ID"] = rid
        if cached is not None and cached.etag:
            headers["If-None-Match"] = cached.etag

        response, problem = self._get_with_retries(path, headers)
        if response is None:
            self.breaker.record_failure()
            return self._fallback(cached, problem or f"{self.name} did not respond")
        self.breaker.record_success()

        if response.status_code == 304 and cached is not None:
            with self._lock:
                cached.fetched_at = self._clock()
            return ClientResult(value=cached.value)  # type: ignore[arg-type]

        if response.status_code != 200:
            logger.warning("%s %s answered %s", self.name, path, response.status_code)
            return self._fallback(cached, f"{self.name} answered {response.status_code} for {path}")

        try:
            value = model.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            # A change on the other side that breaks the agreement. Loud in the logs,
            # and the bad answer is neither used nor kept.
            logger.error("Contract error: %s %s no longer matches %s: %s", self.name, path, model.__name__, exc)
            return self._fallback(cached, f"{self.name} {path} did not match the agreed contract")

        with self._lock:
            self._cache[key] = _CacheEntry(value=value, etag=response.headers.get("etag"), fetched_at=self._clock())
        return ClientResult(value=value)

    def post(self, path: str, body: dict, model: type[T]) -> ClientResult[T]:
        """For questions the other service answers by computing (is this operation
        allowed?). The answer depends on the body, so nothing is cached, and an old
        answer is never served in place of a missing one: that would be a verdict
        about different facts."""
        if not self.breaker.allow():
            return ClientResult(unavailable_reason=f"{self.name} is not responding (circuit open)")

        headers = {}
        rid = request_id.get()
        if rid:
            headers["X-Request-ID"] = rid

        response, problem = self._get_with_retries(path, headers, json_body=body)
        if response is None:
            self.breaker.record_failure()
            return ClientResult(unavailable_reason=problem or f"{self.name} did not respond")
        self.breaker.record_success()

        if response.status_code != 200:
            logger.warning("%s %s answered %s", self.name, path, response.status_code)
            return ClientResult(unavailable_reason=f"{self.name} answered {response.status_code} for {path}")

        try:
            return ClientResult(value=model.model_validate(response.json()))
        except (ValueError, ValidationError) as exc:
            logger.error("Contract error: %s %s no longer matches %s: %s", self.name, path, model.__name__, exc)
            return ClientResult(unavailable_reason=f"{self.name} {path} did not match the agreed contract")

    # -- internals --

    def _get_with_retries(
        self, path: str, headers: dict[str, str], json_body: Optional[dict] = None
    ) -> tuple[Optional[httpx.Response], Optional[str]]:
        problem = None
        for attempt in range(self.retries + 1):
            try:
                if json_body is None:
                    response = self._http.get(path, headers=headers)
                else:
                    response = self._http.post(path, json=json_body, headers=headers)
            except httpx.TimeoutException:
                problem = f"{self.name} timed out"
            except httpx.TransportError as exc:
                problem = f"{self.name} could not be reached ({type(exc).__name__})"
            else:
                if response.status_code < 500:
                    return response, None
                problem = f"{self.name} answered {response.status_code}"
            if attempt < self.retries:
                self._sleep(self.backoff_seconds * (2**attempt))
        logger.warning("%s %s failed after %d attempt(s): %s", self.name, path, self.retries + 1, problem)
        return None, problem

    @staticmethod
    def _fallback(cached: Optional[_CacheEntry], reason: str) -> "ClientResult":
        if cached is not None:
            return ClientResult(value=cached.value, stale=True, unavailable_reason=reason)
        return ClientResult(unavailable_reason=reason)
