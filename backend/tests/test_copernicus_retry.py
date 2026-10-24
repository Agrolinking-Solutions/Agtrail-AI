"""
Copernicus answers 429 when it is asked too fast. The hub waits and tries again, and treats a
real error as a real error. No network is used and no time passes.
"""

import httpx
import pytest

import app.services.sentinel as sentinel


class Reply:
    def __init__(self, status, headers=None):
        self.status_code, self.headers = status, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


@pytest.fixture
def waits(monkeypatch):
    recorded = []
    monkeypatch.setattr(sentinel, "_sleep", recorded.append)
    return recorded


def script(monkeypatch, *replies):
    calls = []
    queue = list(replies)

    def post(url, **kwargs):
        calls.append(url)
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(sentinel.httpx, "post", post)
    return calls


def test_a_good_answer_is_returned_without_waiting(monkeypatch, waits):
    calls = script(monkeypatch, Reply(200))

    assert sentinel._post_with_retries("https://x").status_code == 200
    assert len(calls) == 1 and waits == []


def test_too_many_requests_is_waited_out_with_growing_pauses_and_then_succeeds(monkeypatch, waits):
    calls = script(monkeypatch, Reply(429), Reply(429), Reply(200))

    assert sentinel._post_with_retries("https://x").status_code == 200
    assert len(calls) == 3 and waits == [2.0, 4.0]


def test_the_wait_copernicus_asks_for_is_respected_but_capped(monkeypatch, waits):
    script(monkeypatch, Reply(429, {"Retry-After": "7"}), Reply(429, {"Retry-After": "900"}), Reply(200))

    sentinel._post_with_retries("https://x")

    assert waits == [7.0, 60.0]


def test_a_server_error_and_a_dropped_connection_are_retried(monkeypatch, waits):
    calls = script(monkeypatch, Reply(503), httpx.ConnectError("down"), httpx.ReadTimeout("slow"), Reply(200))

    assert sentinel._post_with_retries("https://x").status_code == 200
    assert len(calls) == 4


def test_a_real_error_is_not_retried(monkeypatch, waits):
    calls = script(monkeypatch, Reply(400))

    assert sentinel._post_with_retries("https://x").status_code == 400
    assert len(calls) == 1 and waits == []


def test_after_the_last_attempt_the_answer_or_the_error_comes_back(monkeypatch, waits):
    calls = script(monkeypatch, Reply(429))
    assert sentinel._post_with_retries("https://x").status_code == 429
    assert len(calls) == sentinel.MAX_ATTEMPTS and len(waits) == sentinel.MAX_ATTEMPTS - 1

    script(monkeypatch, httpx.ConnectError("down"))
    with pytest.raises(httpx.ConnectError):
        sentinel._post_with_retries("https://x")


def test_a_person_waiting_for_a_picture_gets_fewer_tries(monkeypatch, waits):
    calls = script(monkeypatch, Reply(429))

    sentinel._post_with_retries("https://x", max_attempts=2)

    assert len(calls) == 2


def test_the_ingest_functions_use_the_retrying_call(monkeypatch, waits):
    from datetime import date

    calls = script(monkeypatch, Reply(429), Reply(200))
    monkeypatch.setattr(Reply, "json", lambda self: {"data": []}, raising=False)

    assert sentinel.fetch_farm_scenes({"type": "Polygon", "coordinates": []}, date(2026, 10, 1), date(2026, 10, 20), "token") == []
    assert len(calls) == 2
