"""Sentinel-1 radar passes. The Copernicus responses have the shape the Statistical API returns for
the scene numbers (the same shape as the optical ones); the network itself is not touched."""

import math
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from tests.conftest import API_KEY, requires_db


def _day(day, vv, vh, grid=100, outside=89):
    def stats(mean):
        return {"bands": {"B0": {"stats": {"mean": mean, "sampleCount": grid, "noDataCount": outside}}}}

    return {"interval": {"from": f"{day}T00:00:00Z", "to": f"{day}T23:59:59Z"}, "outputs": {"vv": stats(vv), "vh": stats(vh)}}


def test_a_pass_becomes_decibels_and_a_vegetation_index():
    from app.services.sentinel import RadarScene, parse_radar_response

    scenes = parse_radar_response({"data": [_day("2026-10-20", 0.10, 0.025)]})

    assert scenes == [RadarScene(scene_date=date(2026, 10, 20), vv_db=-10.0, vh_db=round(10 * math.log10(0.025), 3),
                                 rvi=round(4 * 0.025 / (0.10 + 0.025), 4), polygon_pixels=11)]


@pytest.mark.parametrize("vv,vh,outside", [(None, 0.02, 89), (0.1, None, 89), (0.0, 0.02, 89), (0.1, -0.1, 89), (0.1, 0.02, 100)])
def test_a_day_without_usable_backscatter_is_skipped(vv, vh, outside):
    from app.services.sentinel import parse_radar_response

    assert parse_radar_response({"data": [_day("2026-10-20", vv, vh, outside=outside)]}) == []


def test_the_request_asks_for_terrain_corrected_radar_over_the_farm(monkeypatch):
    import json

    from app.services import sentinel

    seen = {}

    class Reply:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": []}

    def fake_post(url, **kw):
        seen["url"], seen["body"] = url, json.loads(kw["content"])
        return Reply()

    monkeypatch.setattr(sentinel, "_post_with_retries", fake_post)
    sentinel.fetch_farm_radar({"type": "Polygon", "coordinates": []}, date(2026, 9, 1), date(2026, 10, 1), "token")

    data = seen["body"]["input"]["data"][0]
    assert seen["url"] == sentinel.STATISTICS_URL and data["type"] == "sentinel-1-grd"
    assert data["dataFilter"]["acquisitionMode"] == "IW" and data["dataFilter"]["polarization"] == "DV"
    assert data["processing"]["backCoeff"] == "GAMMA0_TERRAIN" and data["processing"]["orthorectify"] is True
    assert seen["body"]["aggregation"]["aggregationInterval"] == {"of": "P1D"}


pytestmark_db = requires_db


@pytest.fixture
def mapped_farm(db_session):
    from geoalchemy2 import WKTElement

    from app.models import Farm, Farmer

    farmer = Farmer(full_name="T", phone="1", state="Oyo", lga="x", agtrail_farmer_id="t-radar")
    db_session.add(farmer); db_session.flush()
    farm = Farm(farmer_id=farmer.id, name="f", agtrail_farm_id="t-radar-farm", boundary_mapped=True,
                boundary=WKTElement("POLYGON((3.90 7.60, 3.92 7.60, 3.92 7.63, 3.90 7.63, 3.90 7.60))", srid=4326))
    db_session.add(farm); db_session.commit()
    return farm


@requires_db
def test_the_job_saves_each_pass_once_and_a_failing_farm_does_not_stop_it(db_session, mapped_farm, monkeypatch):
    from app.jobs import ingest_radar as job
    from app.models import FarmRadarScene
    from app.services.sentinel import RadarScene

    monkeypatch.setattr(job, "get_access_token", lambda: "token")
    calls = []

    def fetch(boundary, start, end, token):
        calls.append((start, end))
        return [RadarScene(date(2026, 10, 20), -10.0, -16.0, 0.2, 40), RadarScene(date(2026, 10, 26), -9.5, -15.5, 0.21, 40)]

    monkeypatch.setattr(job, "fetch_farm_radar", fetch)
    first = job.ingest_radar(end=date(2026, 10, 27))
    second = job.ingest_radar(end=date(2026, 10, 27))

    rows = db_session.execute(select(FarmRadarScene)).scalars().all()
    assert first["scenes_written"] == 2 and len(rows) == 2 and second["farms_failed"] == 0
    # A farm with no history gets the long first window, one that has history gets the recent one.
    assert (calls[0][1] - calls[0][0]).days == job.FIRST_RUN_DAYS and (calls[1][1] - calls[1][0]).days == job.RECENT_DAYS

    monkeypatch.setattr(job, "fetch_farm_radar", lambda *a: (_ for _ in ()).throw(RuntimeError("Copernicus is down")))
    assert job.ingest_radar(end=date(2026, 10, 27))["farms_failed"] == 1


@requires_db
def test_the_radar_endpoint_says_fresh_stale_or_no_data(client, db_session, mapped_farm):
    from app.models import FarmRadarScene

    headers = {"X-API-Key": API_KEY}
    empty = client.get(f"/farms/{mapped_farm.id}/radar", headers=headers).json()
    assert empty["status"] == "no_data" and empty["latest"] is None and empty["series"] == [] and empty["provenance"] == "observed"

    today = datetime.now(timezone.utc).date()
    for days_ago, vh in [(40, -17.0), (9, -16.0)]:
        db_session.add(FarmRadarScene(farm_id=mapped_farm.id, scene_date=today - timedelta(days=days_ago), vv_db=-10.0, vh_db=vh, rvi=0.2, polygon_pixels=40))
    db_session.commit()
    fresh = client.get(f"/farms/{mapped_farm.id}/radar", headers=headers).json()
    assert fresh["status"] == "fresh" and fresh["latest"]["age_days"] == 9 and [p["vh_db"] for p in fresh["series"]] == [-17.0, -16.0]
    assert "sees through cloud" in fresh["note"]

    db_session.execute(FarmRadarScene.__table__.delete().where(FarmRadarScene.scene_date > today - timedelta(days=20)))
    db_session.commit()
    stale = client.get(f"/farms/{mapped_farm.id}/radar", headers=headers).json()
    assert stale["status"] == "stale" and stale["latest"]["age_days"] == 40


def test_a_radar_failure_never_stops_the_daily_satellite_task(monkeypatch):
    from app.jobs import ingest_ndvi, ingest_radar

    monkeypatch.setattr(ingest_ndvi, "ingest_scenes", lambda *a, **k: {"farms": 0})
    monkeypatch.setattr(ingest_ndvi, "ingest_ndvi", lambda *a, **k: {"farms": 0})
    monkeypatch.setattr(ingest_radar, "ingest_radar", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("radar is down")))

    ingest_ndvi.main()
