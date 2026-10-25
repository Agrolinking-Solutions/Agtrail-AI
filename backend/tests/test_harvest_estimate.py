"""
The harvest estimate: how much, when and what it is worth, as a range, always an estimate. Built from
real captured Knowledge Base and PIS responses, so no network is used. Every figure that cannot be worked
out is left out with its reason.
"""

import json
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.clients.base import ClientResult
from app.contracts.knowledge_base import CommodityMapping, HarvestEstimateBasis
from app.contracts.pis import CommodityForecast
from app.services.harvest_estimate import build_estimate
from tests.conftest import API_KEY, requires_db

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 23)


def kb_fixture(name):
    return json.loads((FIXTURES / "knowledge_base" / f"{name}.json").read_text())


class FakeKb:
    def __init__(self, down=False):
        self.down = down

    def harvest_estimate(self, crop_id):
        if self.down:
            return ClientResult(unavailable_reason="Knowledge Base timed out")
        path = FIXTURES / "knowledge_base" / f"harvest_estimate_{crop_id}.json"
        if not path.exists():
            return ClientResult(value=HarvestEstimateBasis(crop_id=crop_id, available=False, reason="No current research is recorded for this crop yet, so no estimate is given."))
        return ClientResult(value=HarvestEstimateBasis.model_validate(json.loads(path.read_text())))

    def commodities(self):
        return ClientResult(value=CommodityMapping.model_validate(kb_fixture("commodities")))


class FakePis:
    def __init__(self, down=False):
        self.down = down

    def forecast(self, commodity):
        if self.down:
            return ClientResult(unavailable_reason="PIS timed out")
        return ClientResult(value=CommodityForecast.model_validate(json.loads((FIXTURES / "pis" / "forecast_maize.json").read_text())))


def estimate(crop_id="maize", area=0.33, planted=TODAY - timedelta(days=60), transplanted=None, kb=None, pis=None, crop="Maize"):
    return build_estimate(crop_id=crop_id, crop_name=crop, area_ha=area, planted_on=planted, transplanted_on=transplanted,
                          kb=kb or FakeKb(), pis=pis or FakePis(), today=TODAY)


def test_the_amount_is_the_farm_size_times_the_typical_and_normal_yields():
    e = estimate()

    assert e.status == "ok" and e.provenance == "estimated"
    a = e.amount
    assert (a.area_ha, a.per, a.yield_basis) == (0.33, "season", "grain")
    assert (a.typical_tonnes, a.low_tonnes, a.high_tonnes) == (round(0.33 * 2.03, 2), round(0.33 * 0.8, 2), round(0.33 * 5.0, 2))
    assert (a.per_hectare.typical, a.per_hectare.low, a.per_hectare.high) == (2.03, 0.8, 5.0)
    assert a.low_tonnes < a.typical_tonnes < a.high_tonnes


def test_the_timing_is_the_planting_date_plus_the_expected_window_and_says_where_today_stands():
    planted = TODAY - timedelta(days=60)

    before = estimate(planted=planted).timing
    inside = estimate(planted=TODAY - timedelta(days=100)).timing
    past = estimate(planted=TODAY - timedelta(days=200)).timing

    assert (before.earliest, before.latest) == (planted + timedelta(days=90), planted + timedelta(days=130))
    assert (before.state, before.days_to_earliest, before.days_to_latest) == ("before_window", 30, 70)
    assert inside.state == "in_window" and inside.days_to_earliest < 0 < inside.days_to_latest
    assert past.state == "after_window" and past.days_to_latest < 0


def test_the_worth_is_the_tonnes_times_the_price_and_carries_the_prices_label_and_age():
    e = estimate()

    per_tonne = 450.0 * 1000 * 1.0
    assert e.value.typical == round(e.amount.typical_tonnes * per_tonne, -3)
    assert e.value.low < e.value.typical < e.value.high
    assert (e.value.price_provenance, e.value.price_age_days) == ("estimated", 18)
    assert "carried forward" in e.value_note


def test_rice_has_a_tonnes_estimate_but_no_naira_value_until_the_milling_factor_is_sourced():
    e = estimate("rice", area=1.0, transplanted=TODAY - timedelta(days=30), crop="Rice")

    assert e.amount.yield_basis.startswith("paddy") and e.value is None and "not been sourced" in e.value_note


def test_rice_timing_is_counted_from_transplanting_and_waits_for_that_date():
    without = estimate("rice", area=1.0, transplanted=None, crop="Rice")
    with_date = estimate("rice", area=1.0, transplanted=date(2026, 9, 1), crop="Rice")

    assert without.timing is None and "transplanting date is not recorded" in without.timing_note
    assert (with_date.timing.counted_from, with_date.timing.earliest, with_date.timing.latest) == ("transplanting", date(2026, 9, 1) + timedelta(days=79), date(2026, 9, 1) + timedelta(days=119))


def test_cassava_has_no_value_because_it_has_no_price_series():
    e = estimate("cassava", area=0.5, planted=TODAY - timedelta(days=100), crop="Cassava")

    assert e.value is None and "no price series" in e.value_note
    assert (e.timing.earliest - (TODAY - timedelta(days=100))).days == 270


def test_cocoa_is_per_year_with_no_single_harvest_date():
    e = estimate("cocoa", area=2.0, crop="Cocoa")

    assert e.amount.per == "year" and e.amount.yield_basis == "dry beans" and e.timing is None and "every year" in e.timing_note
    assert (e.amount.typical_tonnes, e.amount.low_tonnes, e.amount.high_tonnes) == (0.54, 0.2, 3.0)


@pytest.mark.parametrize("kwargs, code", [
    ({"crop_id": None}, "no_crop"),
    ({"crop_id": "yam", "crop": "Yam"}, "no_research"),
    ({"area": None}, "no_area"),
    ({"area": 0}, "no_area"),
    ({"kb": FakeKb(down=True)}, "estimate_unavailable"),
])
def test_when_an_estimate_cannot_be_made_it_says_why_and_gives_no_numbers(kwargs, code):
    e = estimate(**kwargs)

    assert e.status == "no_estimate" and e.reason_code == code and e.reason
    assert e.amount is None and e.timing is None and e.value is None and e.sources == []


def test_a_missing_price_leaves_out_the_value_but_keeps_the_amount_and_timing():
    e = estimate(pis=FakePis(down=True))

    assert e.amount is not None and e.timing is not None and e.value is None and "PIS unavailable" in e.value_note


def test_the_sources_assumptions_and_cautions_travel_with_every_estimate():
    e = estimate()

    assert e.as_of == "2026-10-25" and 0 < e.confidence <= 1
    assert any("FAOSTAT" in s.title for s in e.sources) and all(s.figure and s.year for s in e.sources)
    assert any("whole recorded farm size" in a for a in e.assumptions)
    assert any("farm" in c.lower() for c in e.caveats) and "not a forecast" in e.method.lower()


# ---------------- the endpoint and the farm summary ----------------

def _farm(db_session, area, crop_id="maize", crop_raw="Maize", planted=None, suffix="he1", with_cycle=True):
    from app.models.crop_cycle import CropCycle
    from app.models.farm import Farm
    from app.models.farmer import Farmer

    farmer = Farmer(full_name="Test Farmer", phone=f"0801{suffix}", state="Kano", lga="Kano Municipal")
    db_session.add(farmer)
    db_session.flush()
    farm = Farm(farmer_id=farmer.id, name="Test Farm", agtrail_farm_id=f"AGT-{suffix}", area_hectares=area)
    db_session.add(farm)
    db_session.flush()
    if with_cycle:
        db_session.add(CropCycle(farm_id=farm.id, agtrail_cycle_id=f"CYC-{suffix}", crop_raw=crop_raw, crop_id=crop_id,
                                 planted_date=planted or date.today() - timedelta(days=60), status="active"))
    db_session.commit()
    return farm


def _get(client, farm_id):
    from app.api.routes.farms import knowledge_base, pis
    from app.main import app

    app.dependency_overrides[knowledge_base] = lambda: FakeKb()
    app.dependency_overrides[pis] = lambda: FakePis()
    try:
        return client.get(f"/farms/{farm_id}/harvest-estimate", headers={"X-API-Key": API_KEY})
    finally:
        app.dependency_overrides.pop(knowledge_base, None)
        app.dependency_overrides.pop(pis, None)


@requires_db
def test_the_endpoint_returns_the_estimate_for_the_newest_crop_with_rules(client, db_session):
    farm = _farm(db_session, 0.33)

    body = _get(client, farm.id).json()

    assert body["status"] == "ok" and body["farm_id"] == str(farm.id) and body["provenance"] == "estimated"
    assert body["amount"]["typical_tonnes"] == round(0.33 * 2.03, 2) and body["timing"]["state"] == "before_window"
    assert body["value"]["price_provenance"] == "estimated" and body["sources"]


@requires_db
def test_the_endpoint_says_what_is_missing_instead_of_guessing(client, db_session):
    no_area = _get(client, _farm(db_session, None, suffix="he2").id).json()
    no_cycle = _get(client, _farm(db_session, 0.5, suffix="he3", with_cycle=False).id).json()
    no_crop = _get(client, _farm(db_session, 0.5, crop_id=None, crop_raw="Dragon fruit", suffix="he4").id).json()
    no_research = _get(client, _farm(db_session, 0.5, crop_id="yam", crop_raw="Yam", suffix="he5").id).json()

    assert (no_area["reason_code"], no_cycle["reason_code"], no_crop["reason_code"], no_research["reason_code"]) == ("no_area", "no_cycle", "no_crop", "no_research")
    assert all(b["amount"] is None and b["value"] is None for b in (no_area, no_cycle, no_crop, no_research))


@requires_db
def test_rice_timing_uses_the_logged_transplanting_date(client, db_session):
    from app.models.crop_cycle import CropCycle
    from app.models.farm_operation import FarmOperation

    farm = _farm(db_session, 1.0, crop_id="rice", crop_raw="Rice", suffix="he6")
    cycle = db_session.query(CropCycle).filter_by(farm_id=farm.id).one()
    db_session.add(FarmOperation(farm_id=farm.id, crop_cycle_id=cycle.id, logged_type_raw="transplanting", signal_category="planting",
                                 kb_operation_id="transplanting", operation_date=date(2026, 9, 1)))
    db_session.commit()

    body = _get(client, farm.id).json()

    assert body["timing"]["counted_from"] == "transplanting" and body["timing"]["counted_from_date"] == "2026-09-01"


@requires_db
def test_unknown_farm_is_404_and_the_key_is_required(client):
    assert _get(client, uuid.uuid4()).status_code == 404
    assert client.get(f"/farms/{uuid.uuid4()}/harvest-estimate").status_code in (401, 403)


def test_the_farm_summary_includes_the_estimate_for_the_newest_crop():
    from app.models.crop_cycle import CropCycle
    from app.services.farm_intelligence import build_intelligence
    from tests.test_farm_intelligence import FakeKb as SummaryKb
    from tests.test_farm_intelligence import FakePis as SummaryPis

    class Both(SummaryKb, FakeKb):
        pass

    older = CropCycle(id=uuid.uuid4(), farm_id=uuid.uuid4(), agtrail_cycle_id="a", crop_raw="Maize", crop_id="maize", planted_date=date(2026, 3, 1), status="done")
    newer = CropCycle(id=uuid.uuid4(), farm_id=uuid.uuid4(), agtrail_cycle_id="b", crop_raw="Cassava", crop_id="cassava", planted_date=date(2026, 8, 1), status="active")

    out = build_intelligence(uuid.uuid4(), "Kano", [older, newer], [], [], Both(), SummaryPis(), TODAY, area_ha=0.5)

    assert out.harvest_estimate.crop_id == "cassava" and out.harvest_estimate.amount.area_ha == 0.5
    assert build_intelligence(uuid.uuid4(), "Kano", [], [], [], Both(), SummaryPis(), TODAY).harvest_estimate is None
