"""
The PIS client, its contract models and the crop price view. The network is
simulated from responses captured from the live service, so nothing here needs a
database or a connection.
"""

import json
from datetime import date
from pathlib import Path

import httpx

from app.clients.base import ClientResult
from app.clients.pis import PisClient
from app.clients.resilience import CircuitBreaker
from app.contracts.export_schemas import PIS_CONTRACTS_DIR, render_pis_schemas
from app.contracts.knowledge_base import CommodityMapping, CommodityRow
from app.contracts.pis import CommodityForecast, Outlook30d, PisHealth, Seasonality, WeatherToday
from app.services.prices import crop_price

FIXTURES = Path(__file__).parent / "fixtures" / "pis"
TODAY = date(2026, 10, 23)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


class FakeClock:
    now = 1000.0

    def __call__(self):
        return self.now


class Server:
    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


def make(server):
    clock = FakeClock()
    return PisClient("https://pis.test", transport=httpx.MockTransport(server), clock=clock, sleep=lambda s: None, breaker=CircuitBreaker(clock=clock))


def ok(name):
    return httpx.Response(200, json=fixture(name))


# ---------------- contract models against the real responses ----------------

def test_the_real_responses_parse_into_the_contract_models():
    assert PisHealth.model_validate(fixture("health")).status == "operational"
    forecast = CommodityForecast.model_validate(fixture("forecast_maize"))
    assert forecast.commodity == "Maize (white)" and forecast.today_price_per_unit == 450.0
    assert forecast.latest_real_price.source == "Web price search" and forecast.start_is_estimate is True
    assert set(forecast.horizons) == {"daily", "weekly", "2_weeks", "monthly", "3_months", "6_months"}
    outlook = Outlook30d.model_validate(fixture("outlook_30d"))
    assert outlook.per_commodity["Sorghum"].signal == "bearish"
    season = Seasonality.model_validate(fixture("seasonality_maize"))
    assert season.monthly_profile["Oct"].is_current and 0 <= season.current_score <= 100
    weather = WeatherToday.model_validate(fixture("weather_today"))
    assert weather.for_state("kano").zone == "North West" and len(weather.for_state("Kano").days) == 7
    assert weather.attribution == "Data from MET Norway"


def test_new_fields_from_pis_do_not_break_the_hub():
    data = fixture("health")
    data["something_new"] = {"added": "later"}
    assert PisHealth.model_validate(data).status


def test_the_committed_json_schemas_match_the_models():
    # Fails if a model changed without re-running: python -m app.contracts.export_schemas
    for filename, text in render_pis_schemas().items():
        assert (PIS_CONTRACTS_DIR / filename).read_text() == text, filename


# ---------------- the client ----------------

def test_the_forecast_is_read_with_the_commodity_name_made_url_safe_and_then_cached():
    server = Server(ok("forecast_maize"))
    client = make(server)

    first = client.forecast("Maize (white)")
    client.forecast("Maize (white)")

    assert first.ok and len(server.requests) == 1
    assert server.requests[0].url.raw_path == b"/forecasts/Maize%20%28white%29"


def test_outlook_weather_seasonality_and_health_use_their_own_paths():
    server = Server(ok("outlook_30d"), ok("weather_today"), ok("seasonality_maize"), ok("health"))
    client = make(server)

    assert client.outlook().ok and client.weather().ok and client.seasonality("Maize (white)").ok and client.health().ok

    assert [r.url.path for r in server.requests] == ["/outlook/30d", "/weather/today", "/seasonality/Maize (white)", "/health"]


def test_when_pis_goes_down_the_last_copy_is_served_as_stale():
    server = Server(ok("outlook_30d"), httpx.ConnectError("down"))
    client = make(server)
    client.outlook()
    FakeClock.now += 3601

    result = client.outlook()

    assert result.ok and result.stale and "could not be reached" in result.unavailable_reason
    FakeClock.now = 1000.0


def test_a_response_that_breaks_the_contract_is_not_used():
    broken = fixture("outlook_30d")
    del broken["per_commodity"]

    result = make(Server(httpx.Response(200, json=broken))).outlook()

    assert not result.ok and "contract" in result.unavailable_reason


# ---------------- the crop price view ----------------

def row(crop_id, pis=None, status="priced", note="Price is per kg."):
    return CommodityRow(crop_id=crop_id, pis_commodity=pis, conversion_factor=None, conversion_note=note, price_status=status, review_status="reviewed-by-data")


class FakeKb:
    def __init__(self, *rows, down=False):
        self.rows, self.down = rows, down

    def commodities(self):
        if self.down:
            return ClientResult(unavailable_reason="Knowledge Base timed out")
        return ClientResult(value=CommodityMapping(crops=list(self.rows)))


class FakePis:
    def __init__(self, data=None, stale=False, down=False):
        self.data, self.stale, self.down, self.asked = data, stale, down, []

    def forecast(self, commodity):
        self.asked.append(commodity)
        if self.down:
            return ClientResult(unavailable_reason="PIS timed out")
        return ClientResult(value=CommodityForecast.model_validate(self.data or fixture("forecast_maize")), stale=self.stale, unavailable_reason="PIS could not be reached" if self.stale else None)


def test_a_carried_forward_price_is_called_estimated_and_carries_its_real_date_and_source():
    pis = FakePis()

    price = crop_price("maize", FakeKb(row("maize", "Maize (white)")), pis, TODAY)

    assert pis.asked == ["Maize (white)"]
    assert (price.status, price.provenance, price.price_ngn_per_kg) == ("priced", "estimated", 450.0)
    assert (price.last_real_price_date, price.last_real_price_source, price.last_real_price_age_days) == (date(2026, 10, 5), "Web price search", 18)
    assert "carried forward" in price.note


def test_a_price_is_only_observed_when_pis_says_it_is_not_an_estimate():
    data = fixture("forecast_maize")
    data["start_is_estimate"] = False

    price = crop_price("maize", FakeKb(row("maize", "Maize (white)")), FakePis(data), TODAY)

    assert price.provenance == "observed" and price.note is None


def test_a_crop_with_no_price_series_has_no_number_and_pis_is_not_asked():
    pis = FakePis()

    price = crop_price("rice_x", FakeKb(row("rice_x", None, status="unpriced", note="No series.")), pis, TODAY)

    assert (price.status, price.price_ngn_per_kg, price.mapping_note) == ("unpriced", None, "No series.")
    assert pis.asked == []


def test_a_crop_missing_from_the_mapping_is_unpriced_not_an_error():
    assert crop_price("nothing", FakeKb(), FakePis(), TODAY).status == "unpriced"


def test_when_a_service_is_down_the_answer_says_so_and_gives_no_number():
    kb_down = crop_price("maize", FakeKb(down=True), FakePis(), TODAY)
    pis_down = crop_price("maize", FakeKb(row("maize", "Maize (white)")), FakePis(down=True), TODAY)

    assert (kb_down.status, kb_down.price_ngn_per_kg) == ("unavailable", None) and "Knowledge Base unavailable" in kb_down.note
    assert (pis_down.status, pis_down.price_ngn_per_kg) == ("unavailable", None) and "PIS unavailable" in pis_down.note


def test_a_stale_copy_of_the_price_says_that_it_is_stale():
    price = crop_price("maize", FakeKb(row("maize", "Maize (white)")), FakePis(stale=True), TODAY)

    assert price.status == "priced" and "could not be reached just now" in price.note


def test_a_price_not_quoted_per_kilogram_is_not_presented_as_per_kilogram():
    data = fixture("forecast_maize")
    data["unit_label"] = "NGN/bag"

    price = crop_price("maize", FakeKb(row("maize", "Maize (white)")), FakePis(data), TODAY)

    assert price.price_ngn_per_kg is None and "NGN/bag" in price.note
