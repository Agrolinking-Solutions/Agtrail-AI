"""
The farm intelligence summary: every block labelled, nothing filled in to look
complete, and one source being down never takes the rest of the summary with it.
"""

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.clients.base import ClientResult
from app.contracts.knowledge_base import CommodityMapping, CommodityRow
from app.contracts.pis import CommodityForecast, Outlook30d, WeatherToday
from app.models.crop_cycle import CropCycle
from app.models.farm_operation import FarmOperation
from app.models.ndvi import FarmNdviScene
from app.models.operation_signal import OperationSignal
from app.services.farm_intelligence import build_intelligence
from tests.conftest import API_KEY, requires_db

PIS = Path(__file__).parent / "fixtures" / "pis"
TODAY = date(2026, 10, 23)
FARM = uuid.uuid4()


def pis_fixture(name):
    return json.loads((PIS / f"{name}.json").read_text())


class FakeKb:
    def __init__(self, down=False):
        self.down = down

    def commodities(self):
        if self.down:
            return ClientResult(unavailable_reason="Knowledge Base timed out")
        row = CommodityRow(crop_id="maize", pis_commodity="Maize (white)", conversion_factor=None, conversion_note="Per kg.", price_status="priced", review_status="reviewed-by-data")
        unpriced = CommodityRow(crop_id="yam", pis_commodity=None, conversion_factor=None, conversion_note="No series.", price_status="unpriced", review_status="reviewed-by-data")
        return ClientResult(value=CommodityMapping(crops=[row, unpriced]))


class FakePis:
    def __init__(self, down=False):
        self.down = down

    def _result(self, model, name):
        if self.down:
            return ClientResult(unavailable_reason="PIS timed out")
        return ClientResult(value=model.model_validate(pis_fixture(name)))

    def forecast(self, commodity):
        return self._result(CommodityForecast, "forecast_maize")

    def outlook(self):
        return self._result(Outlook30d, "outlook_30d")

    def weather(self):
        return self._result(WeatherToday, "weather_today")


def cycle(crop="Maize", crop_id="maize", planted=date(2026, 8, 20)):
    return CropCycle(id=uuid.uuid4(), farm_id=FARM, agtrail_cycle_id=str(uuid.uuid4()), crop_raw=crop, crop_id=crop_id, planted_date=planted, status="active")


def scene(days_ago, ndvi, clear=1.0):
    return FarmNdviScene(farm_id=FARM, scene_date=TODAY - timedelta(days=days_ago), ndvi_mean=ndvi, clear_fraction=clear, polygon_pixels=20)


def signal(c, status, issues, day, kb_op="weeding_1"):
    op = FarmOperation(id=uuid.uuid4(), farm_id=FARM, crop_cycle_id=c.id, logged_type_raw="weeding", operation_date=day, created_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
    sig = OperationSignal(operation_id=op.id, farm_id=FARM, crop_cycle_id=c.id, status=status, kb_operation_id=kb_op, issues=issues, kb_version="0.1.0+abc")
    return sig, op, c


MISSING = {"rule_id": "maize_r1", "severity": "block", "message": "'land_preparation' requires 'land_clearing' to be logged first, and no record of it exists yet."}
EARLY = {"rule_id": "maize_r8", "severity": "block", "message": "Harvest too early. (logged 66 day(s) after 'planting'; minimum is 85.)"}


def build(state="Kano", cycles=None, scenes=None, rows=None, kb=None, pis=None):
    return build_intelligence(FARM, state, cycles if cycles is not None else [], scenes or [], rows or [], kb or FakeKb(), pis or FakePis(), TODAY)


def test_a_complete_farm_gets_every_block_labelled():
    c = cycle()
    rows = [signal(c, "ok", [], date(2026, 8, 20), "planting"), signal(c, "unknown", [MISSING], date(2026, 9, 1)), signal(c, "blocked", [EARLY], date(2026, 10, 1), "harvest")]

    out = build(cycles=[c], scenes=[scene(3, 0.52), scene(6, 0.5), scene(8, None, 0.0)], rows=rows)

    assert [(x.crop, x.crop_id, x.days_since_planting) for x in out.crops] == [("Maize", "maize", 64)]
    assert (out.crop_health.status, out.crop_health.provenance, out.crop_health.latest_clear.age_days) == ("fresh", "observed", 3)
    assert out.signals.provenance == "inferred" and out.signals.kb_version == "0.1.0+abc"
    assert out.signals.counts == {"ok": 1, "unknown": 1, "flagged": 1}
    assert out.signals.record_gaps == 1
    assert [(i.kind, i.crop) for i in out.signals.needs_attention] == [("timing", "Maize")]
    price = out.prices[0]
    assert (price.status, price.provenance, price.price_ngn_per_kg, price.last_real_price_age_days) == ("priced", "estimated", 450.0, 18)
    assert price.outlook_provenance == "estimated" and price.outlook_30d_pct is not None and price.outlook_30d_signal
    assert (out.weather.status, out.weather.provenance, out.weather.city) == ("ok", "estimated", "Kano")
    assert out.weather.attribution == "Data from MET Norway" and len(out.weather.days) == 7


def test_a_job_that_was_not_logged_is_a_record_gap_not_something_that_needs_attention():
    c = cycle()

    out = build(cycles=[c], rows=[signal(c, "blocked", [MISSING], date(2026, 9, 1))])

    assert out.signals.record_gaps == 1 and out.signals.needs_attention == []


def test_two_rules_objecting_in_the_same_way_to_one_operation_show_once():
    c = cycle()
    later = {**EARLY, "rule_id": "maize_r8b", "message": "Flagged. (logged 66 day(s) after 'planting'; minimum is 100.)"}

    out = build(cycles=[c], rows=[signal(c, "blocked", [EARLY, later], date(2026, 10, 1), "harvest")])

    assert [i.kind for i in out.signals.needs_attention] == ["timing"]
    assert "85" in out.signals.needs_attention[0].summary


def test_nothing_known_about_a_farm_says_so_in_each_block():
    out = build(state=None)

    assert out.crops == [] and out.prices == []
    assert out.crop_health.status == "no_data" and out.crop_health.latest_clear is None
    assert out.signals.counts == {} and out.signals.needs_attention == []
    assert out.weather.status == "no_forecast_for_state" and "not known" in out.weather.note


def test_a_state_pis_does_not_cover_gets_no_made_up_forecast():
    out = build(state="Atlantis")

    assert out.weather.status == "no_forecast_for_state" and out.weather.days == []


def test_when_pis_is_down_prices_and_weather_say_so_and_the_rest_is_still_there():
    c = cycle()

    out = build(cycles=[c], scenes=[scene(3, 0.52)], pis=FakePis(down=True))

    assert out.weather.status == "unavailable" and "PIS unavailable" in out.weather.note
    assert out.prices[0].status == "unavailable" and out.prices[0].price_ngn_per_kg is None and out.prices[0].outlook_30d_pct is None
    assert out.crop_health.status == "fresh"


def test_when_the_knowledge_base_is_down_the_price_says_so_and_the_weather_is_unaffected():
    out = build(cycles=[cycle()], kb=FakeKb(down=True))

    assert out.prices[0].status == "unavailable" and "Knowledge Base unavailable" in out.prices[0].note
    assert out.weather.status == "ok"


def test_a_crop_with_no_price_series_is_unpriced_and_each_crop_appears_once():
    out = build(cycles=[cycle(planted=date(2026, 7, 1)), cycle(planted=date(2026, 8, 1)), cycle("Yam", "yam", date(2026, 9, 1)), cycle("Cassava", None, date(2026, 9, 2))])

    assert [(p.crop_id, p.status) for p in out.prices] == [("yam", "unpriced"), ("maize", "priced")]
    assert [c.crop for c in out.crops][:2] == ["Cassava", "Yam"]  # newest first, including a crop with no rules


@requires_db
def test_the_endpoint_returns_the_summary_and_a_404_for_an_unknown_farm(client, db_session):
    from app.api.routes.farms import knowledge_base, pis
    from app.main import app
    from app.models.farm import Farm
    from app.models.farmer import Farmer

    farmer = Farmer(full_name="Test Farmer", phone="0800000099", state="Kano", lga="Kano Municipal")
    db_session.add(farmer)
    db_session.flush()
    farm = Farm(farmer_id=farmer.id, name="Test Farm", agtrail_farm_id="AGT-INTEL-1")
    db_session.add(farm)
    db_session.flush()
    db_session.add(CropCycle(farm_id=farm.id, agtrail_cycle_id="CYC-INTEL-1", crop_raw="Maize", crop_id="maize", planted_date=date.today() - timedelta(days=30), status="active"))
    db_session.commit()
    app.dependency_overrides[knowledge_base] = lambda: FakeKb()
    app.dependency_overrides[pis] = lambda: FakePis()
    try:
        response = client.get(f"/farms/{farm.id}/intelligence", headers={"X-API-Key": API_KEY})
        missing = client.get(f"/farms/{uuid.uuid4()}/intelligence", headers={"X-API-Key": API_KEY})
        no_key = client.get(f"/farms/{farm.id}/intelligence")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["crops"][0]["days_since_planting"] == 30
    assert body["crop_health"]["status"] == "no_data"
    assert body["prices"][0]["provenance"] == "estimated" and body["weather"]["state"] == "Kano"
    assert missing.status_code == 404 and no_key.status_code in (401, 403)
