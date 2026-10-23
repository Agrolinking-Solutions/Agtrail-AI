"""
The latest-usable-reading summary and its endpoint. Dates are fixed so the
freshness rules are checked exactly, with no dependence on today's date.
"""

import uuid
from datetime import date, timedelta

from tests.conftest import API_KEY, requires_db
from app.models.ndvi import FarmNdviScene
from app.services.crop_health import FRESH_WITHIN_DAYS, summarize

TODAY = date(2026, 10, 24)


def scene(days_ago: int, ndvi: float | None, clear: float, pixels: int = 20) -> FarmNdviScene:
    return FarmNdviScene(scene_date=TODAY - timedelta(days=days_ago), ndvi_mean=ndvi, clear_fraction=clear, polygon_pixels=pixels)


def test_the_newest_clear_pass_is_the_reading_and_its_age_is_counted():
    summary = summarize([scene(10, 0.4, 1.0), scene(3, 0.6, 1.0), scene(5, None, 0.0)], TODAY)

    assert summary.status == "fresh"
    assert (summary.latest_clear.ndvi_mean, summary.latest_clear.age_days) == (0.6, 3)
    assert summary.previous_clear.ndvi_mean == 0.4


def test_cloudy_passes_are_skipped_and_never_carried_forward_as_a_number():
    summary = summarize([scene(2, None, 0.0), scene(4, None, 0.0), scene(8, 0.55, 1.0)], TODAY)

    assert summary.latest_clear.age_days == 8
    assert summary.unusable_passes_last_30_days == 2
    assert summary.passes_last_30_days == 3


def test_a_mostly_hidden_farm_is_not_a_usable_reading():
    summary = summarize([scene(1, 0.3, 0.03, pixels=31)], TODAY)

    assert summary.status == "no_clear_reading" and summary.latest_clear is None


def test_half_clear_is_the_minimum_that_counts():
    assert summarize([scene(1, 0.5, 0.5)], TODAY).latest_clear is not None
    assert summarize([scene(1, 0.5, 0.49)], TODAY).latest_clear is None


def test_a_reading_is_fresh_for_two_weeks_then_stale_but_still_reported():
    assert summarize([scene(FRESH_WITHIN_DAYS, 0.5, 1.0)], TODAY).status == "fresh"
    stale = summarize([scene(FRESH_WITHIN_DAYS + 1, 0.5, 1.0)], TODAY)
    assert stale.status == "stale" and stale.latest_clear.age_days == FRESH_WITHIN_DAYS + 1


def test_no_passes_at_all_is_no_data():
    summary = summarize([], TODAY)

    assert summary.status == "no_data"
    assert summary.latest_clear is None and summary.passes_last_30_days == 0


def test_passes_older_than_30_days_are_not_counted_as_recent():
    summary = summarize([scene(29, None, 0.0), scene(30, None, 0.0), scene(45, 0.5, 1.0)], TODAY)

    assert summary.passes_last_30_days == 1


def test_a_pass_dated_in_the_future_is_ignored():
    assert summarize([scene(-2, 0.9, 1.0)], TODAY).status == "no_data"


@requires_db
def test_endpoint_reports_the_latest_clear_reading(client, db_session):
    from app.models.farm import Farm
    from app.models.farmer import Farmer

    farmer = Farmer(full_name="Test Farmer", phone="080000001", state="Oyo", lga="Ibadan North")
    db_session.add(farmer)
    db_session.flush()
    farm = Farm(farmer_id=farmer.id, name="Test Farm", agtrail_farm_id="1fb223df-fe8f-4dfa-83b7-801c3b0f07c5")
    db_session.add(farm)
    db_session.flush()
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date()
    db_session.add(FarmNdviScene(farm_id=farm.id, scene_date=today - timedelta(days=4), ndvi_mean=0.61, clear_fraction=1.0, polygon_pixels=14))
    db_session.add(FarmNdviScene(farm_id=farm.id, scene_date=today - timedelta(days=1), ndvi_mean=None, clear_fraction=0.0, polygon_pixels=14))
    db_session.commit()

    for farm_id in (farm.id, "1fb223df-fe8f-4dfa-83b7-801c3b0f07c5"):
        response = client.get(f"/farms/{farm_id}/crop-health", headers={"X-API-Key": API_KEY})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "fresh" and body["provenance"] == "observed"
        assert body["latest_clear"]["ndvi_mean"] == 0.61 and body["latest_clear"]["age_days"] == 4
        assert body["previous_clear"] is None
        assert (body["passes_last_30_days"], body["unusable_passes_last_30_days"]) == (2, 1)


@requires_db
def test_endpoint_says_no_data_for_a_farm_never_scanned(client, db_session):
    from app.models.farm import Farm
    from app.models.farmer import Farmer

    farmer = Farmer(full_name="Test Farmer", phone="080000002", state="Oyo", lga="Ibadan North")
    db_session.add(farmer)
    db_session.flush()
    farm = Farm(farmer_id=farmer.id, name="Unmapped Farm")
    db_session.add(farm)
    db_session.commit()

    body = client.get(f"/farms/{farm.id}/crop-health", headers={"X-API-Key": API_KEY}).json()

    assert body["status"] == "no_data" and body["latest_clear"] is None


@requires_db
def test_endpoint_404_for_unknown_farm_and_needs_the_api_key(client):
    assert client.get(f"/farms/{uuid.uuid4()}/crop-health", headers={"X-API-Key": API_KEY}).status_code == 404
    assert client.get(f"/farms/{uuid.uuid4()}/crop-health").status_code in (401, 403)
