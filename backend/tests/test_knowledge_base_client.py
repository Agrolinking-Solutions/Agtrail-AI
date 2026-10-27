"""
The Knowledge Base client: contract models, retries, the circuit breaker, caching
and graceful degradation. The network is simulated, so none of this needs a database.
"""

import json
from pathlib import Path

import httpx
import pytest

from app.clients.base import ServiceClient
from app.clients.knowledge_base import KnowledgeBaseClient
from app.clients.resilience import CircuitBreaker, request_id
from app.contracts.export_schemas import CONTRACTS_DIR, render_schemas
from app.contracts.knowledge_base import (
    CommodityMapping,
    KnowledgeBaseVersion,
    RegionMapping,
    Taxonomy,
)

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge_base"
ETAG = '"abc123"'


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds: float):
        self.now += seconds


class Server:
    """A scripted stand-in for the Knowledge Base. Records every request it receives."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


def ok(name="taxonomy", etag=ETAG):
    return httpx.Response(200, json=fixture(name), headers={"etag": etag})


def make(server, clock=None, **options):
    clock = clock or FakeClock()
    options.setdefault("sleep", lambda s: None)
    options.setdefault("breaker", CircuitBreaker(clock=clock))
    return KnowledgeBaseClient("https://kb.test", transport=httpx.MockTransport(server), clock=clock, **options), clock


# ---------------- contract models against the real responses ----------------

def test_the_real_responses_parse_into_the_contract_models():
    taxonomy = Taxonomy.model_validate(fixture("taxonomy"))
    assert len(taxonomy.categories) == 11 and len(taxonomy.operations) == 54
    commodities = CommodityMapping.model_validate(fixture("commodities"))
    assert commodities.for_crop("maize").pis_commodity == "Maize (white)"
    assert commodities.for_crop("rice").conversion_factor is None
    assert commodities.for_crop("cassava").price_status == "unpriced"
    regions = RegionMapping.model_validate(fixture("regions"))
    assert len(regions.states) == 37
    assert regions.for_state("kano").geo_zone == "North West"
    assert KnowledgeBaseVersion.model_validate(fixture("version")).kb_version.startswith("0.1.0+")


def test_taxonomy_lookup_matches_the_knowledge_base_rules():
    taxonomy = Taxonomy.model_validate(fixture("taxonomy"))
    assert taxonomy.signal_category_for("maize", "first_fertilizer") == "fertilizer_application"
    assert taxonomy.signal_category_for(None, "weeding_1") == "weeding"
    assert taxonomy.signal_category_for("maize", "not_a_real_operation") is None


def test_a_crop_override_wins():
    data = fixture("taxonomy")
    data["crop_overrides"] = {"rubber": {"tapping": "other"}}
    taxonomy = Taxonomy.model_validate(data)
    assert taxonomy.signal_category_for("rubber", "tapping") == "other"
    assert taxonomy.signal_category_for("maize", "tapping") == "harvesting"


def test_new_fields_from_the_knowledge_base_do_not_break_the_hub():
    data = fixture("version")
    data["something_new"] = {"added": "later"}
    assert KnowledgeBaseVersion.model_validate(data).kb_version


def test_the_committed_json_schemas_match_the_models():
    # Fails if a model changed without re-running: python -m app.contracts.export_schemas
    for filename, text in render_schemas().items():
        assert (CONTRACTS_DIR / filename).read_text() == text, filename


# ---------------- the client ----------------

def test_a_good_answer_is_parsed_and_returned():
    server = Server(ok())
    client, _ = make(server)
    result = client.taxonomy()
    assert result.ok and not result.stale and result.unavailable_reason is None
    assert len(result.value.operations) == 54


def test_a_fresh_answer_is_served_from_memory_without_another_request():
    server = Server(ok())
    client, clock = make(server)
    client.taxonomy()
    clock.advance(1800)
    assert client.taxonomy().ok
    assert len(server.requests) == 1


def test_an_expired_answer_is_rechecked_with_the_etag_and_a_304_keeps_it():
    server = Server(ok(), httpx.Response(304, headers={"etag": ETAG}))
    client, clock = make(server)
    client.taxonomy()
    clock.advance(3601)
    result = client.taxonomy()
    assert result.ok and not result.stale
    assert server.requests[1].headers["if-none-match"] == ETAG
    clock.advance(1800)
    client.taxonomy()
    assert len(server.requests) == 2  # the 304 refreshed the timer, so no third call yet


def test_a_changed_answer_replaces_the_old_one():
    changed = fixture("version")
    changed["kb_version"] = "0.1.0+newnewnew1"
    server = Server(httpx.Response(200, json=fixture("version"), headers={"etag": '"v1"'}),
                    httpx.Response(200, json=changed, headers={"etag": '"v2"'}))
    client, clock = make(server)
    assert client.version().value.kb_version == "0.1.0+da67b49395"
    clock.advance(61)
    assert client.version().value.kb_version == "0.1.0+newnewnew1"


def test_a_server_error_is_retried_twice_then_succeeds():
    server = Server(httpx.Response(503), httpx.Response(502), ok())
    client, _ = make(server)
    assert client.taxonomy().ok
    assert len(server.requests) == 3


def test_timeouts_are_retried_and_then_reported_as_unavailable():
    server = Server(httpx.ReadTimeout("slow"))
    sleeps = []
    client, _ = make(server, sleep=sleeps.append)
    result = client.taxonomy()
    assert not result.ok and "timed out" in result.unavailable_reason
    assert len(server.requests) == 3
    assert sleeps == [0.25, 0.5]  # backs off between tries


def test_a_4xx_is_a_real_answer_and_is_not_retried():
    server = Server(httpx.Response(404))
    client, _ = make(server)
    result = client.taxonomy()
    assert not result.ok and "404" in result.unavailable_reason
    assert len(server.requests) == 1
    assert client.breaker.state == "closed"  # a missing page is not an outage


def test_when_the_service_goes_down_the_last_good_copy_is_served_as_stale():
    server = Server(ok(), httpx.ConnectError("down"))
    client, clock = make(server)
    client.taxonomy()
    clock.advance(3601)
    result = client.taxonomy()
    assert result.ok and result.stale
    assert "could not be reached" in result.unavailable_reason
    assert len(result.value.operations) == 54


def test_a_response_that_breaks_the_contract_is_logged_and_never_used(monkeypatch):
    # The logging call is checked directly: the database tests' migration step
    # switches off loggers created before it, so caplog is not reliable here.
    import app.clients.base as base

    logged = []
    monkeypatch.setattr(base.logger, "error", lambda message, *args: logged.append(message % args))
    broken = fixture("taxonomy")
    del broken["categories"]
    server = Server(httpx.Response(200, json=broken))
    client, _ = make(server)
    result = client.taxonomy()
    assert not result.ok and "contract" in result.unavailable_reason
    assert len(logged) == 1 and "Contract error" in logged[0] and "Taxonomy" in logged[0]


def test_a_broken_answer_does_not_replace_a_good_cached_one():
    broken = fixture("taxonomy")
    broken["operations"] = "nonsense"
    server = Server(ok(), httpx.Response(200, json=broken))
    client, clock = make(server)
    client.taxonomy()
    clock.advance(3601)
    result = client.taxonomy()
    assert result.ok and result.stale and len(result.value.operations) == 54


def test_an_answer_that_is_not_json_is_a_contract_error_not_a_crash():
    server = Server(httpx.Response(200, content=b"<html>proxy error</html>"))
    client, _ = make(server)
    assert not client.taxonomy().ok


def test_the_request_id_is_passed_on():
    server = Server(ok())
    client, _ = make(server)
    token = request_id.set("req-42")
    try:
        client.taxonomy()
    finally:
        request_id.reset(token)
    assert server.requests[0].headers["x-request-id"] == "req-42"


def test_no_request_id_header_when_there_is_none():
    server = Server(ok())
    client, _ = make(server)
    client.taxonomy()
    assert "x-request-id" not in server.requests[0].headers


# ---------------- circuit breaker ----------------

def test_five_failures_open_the_breaker_and_later_calls_are_refused_without_a_request():
    server = Server(httpx.ConnectError("down"))
    client, clock = make(server, retries=0)
    for _ in range(5):
        assert not client.taxonomy().ok
    assert client.breaker.state == "open"
    calls = len(server.requests)
    result = client.taxonomy()
    assert not result.ok and "circuit open" in result.unavailable_reason
    assert len(server.requests) == calls  # refused instantly, no network call


def test_the_breaker_tries_again_after_the_cooldown_and_closes_on_success():
    server = Server(*([httpx.ConnectError("down")] * 5), ok())
    client, clock = make(server, retries=0)
    for _ in range(5):
        client.taxonomy()
    clock.advance(31)
    assert client.breaker.state == "half-open"
    assert client.taxonomy().ok
    assert client.breaker.state == "closed"


def test_a_failed_trial_reopens_the_breaker():
    server = Server(httpx.ConnectError("down"))
    client, clock = make(server, retries=0)
    for _ in range(5):
        client.taxonomy()
    clock.advance(31)
    assert not client.taxonomy().ok
    assert client.breaker.state == "open"
    calls = len(server.requests)
    client.taxonomy()
    assert len(server.requests) == calls


def test_failures_older_than_a_minute_do_not_count():
    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock)
    for _ in range(4):
        breaker.record_failure()
    clock.advance(61)
    breaker.record_failure()
    assert breaker.state == "closed"


def test_one_call_with_retries_counts_as_one_failure_not_three():
    server = Server(httpx.ConnectError("down"))
    client, _ = make(server)  # default: 2 retries, 3 attempts per call
    for _ in range(4):
        client.taxonomy()
    assert client.breaker.state == "closed"  # 4 failed calls, 12 attempts, still under 5 calls


def test_only_one_trial_call_is_let_through_while_half_open():
    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock)
    for _ in range(5):
        breaker.record_failure()
    clock.advance(31)
    assert breaker.allow() is True
    assert breaker.allow() is False


# ---------------- crop steps, validation and suggestions ----------------

HISTORY = [{"operation_id": "planting", "date": "2026-06-15"}]
HARVEST = {"operation_id": "harvest", "date": "2026-08-20"}


def test_a_crops_steps_are_read_and_cached_like_the_other_reference_data():
    server = Server(ok("crop_steps_sorghum"))
    client, _ = make(server)

    first = client.crop_steps("sorghum")
    client.crop_steps("sorghum")

    assert first.ok and first.value.steps[0].operation_id == "land_clearing"
    assert [s.operation_id for s in first.value.steps_in_category("weeding")] == ["weeding_1", "weeding_2"]
    assert len(server.requests) == 1 and server.requests[0].url.path == "/v1/crops/sorghum/steps"


def test_a_harvest_estimate_is_read_cached_and_a_crop_without_research_says_so():
    from app.contracts.knowledge_base import HarvestEstimateBasis

    server = Server(ok("harvest_estimate_maize"))
    client, _ = make(server)
    first = client.harvest_estimate("maize")
    client.harvest_estimate("maize")

    assert first.ok and first.value.available and first.value.yield_t_per_ha.typical == 2.03 and len(server.requests) == 1
    assert server.requests[0].url.path == "/v1/crops/maize/harvest-estimate"
    none = HarvestEstimateBasis.model_validate(fixture("harvest_estimate_onion"))
    assert none.available is False and none.yield_t_per_ha is None and none.reason


def test_validate_sends_the_history_and_operation_and_parses_the_verdict():
    server = Server(ok("validate_blocked"))
    client, _ = make(server)

    result = client.validate("sorghum", HISTORY, HARVEST, region="Kaduna")

    request = server.requests[0]
    assert request.method == "POST" and request.url.path == "/v1/validate"
    assert json.loads(request.content) == {"crop_id": "sorghum", "history": HISTORY, "new_operation": HARVEST, "region": "Kaduna"}
    assert result.value.blocked and result.value.blocking_issues[0].rule_id == "sorghum_r8"


def test_validate_answers_are_never_cached_because_they_depend_on_the_history():
    server = Server(ok("validate_clean"))
    client, _ = make(server)

    client.validate("sorghum", HISTORY, HARVEST)
    client.validate("sorghum", HISTORY, HARVEST)

    assert len(server.requests) == 2


def test_validate_never_serves_an_old_answer_when_the_service_is_down():
    server = Server(ok("validate_clean"), httpx.ConnectError("down"))
    client, _ = make(server)
    client.validate("sorghum", HISTORY, HARVEST)

    result = client.validate("sorghum", HISTORY, HARVEST)

    assert not result.ok and not result.stale and "could not be reached" in result.unavailable_reason


def test_validate_retries_a_server_error_but_not_a_rejection():
    server = Server(httpx.Response(503), ok("validate_clean"))
    client, _ = make(server)
    assert client.validate("sorghum", HISTORY, HARVEST).ok and len(server.requests) == 2

    rejected = Server(httpx.Response(404, json={"detail": "No crop file"}))
    client, _ = make(rejected)
    result = client.validate("nope", HISTORY, HARVEST)
    assert not result.ok and "404" in result.unavailable_reason and len(rejected.requests) == 1


def test_a_validate_answer_that_breaks_the_contract_is_not_used():
    broken = fixture("validate_clean")
    del broken["blocked"]
    client, _ = make(Server(httpx.Response(200, json=broken)))

    assert "contract" in client.validate("sorghum", HISTORY, HARVEST).unavailable_reason


def test_suggestions_are_parsed_and_the_optional_fields_are_only_sent_when_given():
    server = Server(ok("suggestions"))
    client, _ = make(server)

    result = client.suggestions("sorghum", HISTORY, as_of="2026-06-20")

    assert json.loads(server.requests[0].content) == {"crop_id": "sorghum", "history": HISTORY, "as_of": "2026-06-20"}
    assert result.value.suggestion.operation_id == "weeding_1" and result.value.suggestion.days_until_due == 9


def test_the_default_client_reads_its_address_from_settings(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "KNOWLEDGE_BASE_URL", "https://example.test/")
    client = KnowledgeBaseClient()
    assert str(client._service._http.base_url).rstrip("/") == "https://example.test"
    client.close()
