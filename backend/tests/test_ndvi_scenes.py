"""
Per-pass NDVI with per-pixel cloud masking. The Copernicus responses here have the
shape the live API returned for real farms; the network itself is not touched.
"""

from datetime import date

from sqlalchemy import select

from tests.conftest import requires_db
from app.services.sentinel import (
    CLOUDY_SCL_CLASSES,
    SCENE_EVALSCRIPT,
    NdviScene,
    parse_scenes_response,
)


def _day(day: str, grid: int, outside: int, clear_mean, ndvi_sum_mean) -> dict:
    """grid = pixels on the request grid, outside = pixels outside the farm polygon."""
    def stats(mean):
        return {"bands": {"B0": {"stats": {"mean": mean, "sampleCount": grid, "noDataCount": outside}}}}

    return {
        "interval": {"from": f"{day}T00:00:00Z", "to": f"{day}T23:59:59Z"},
        "outputs": {"clear": stats(clear_mean), "ndvi_clear": stats(ndvi_sum_mean)},
    }


def test_a_fully_clear_pass_gives_the_mean_ndvi_and_the_real_pixel_count():
    payload = {"data": [_day("2026-09-15", 100, 89, 1.0, 0.601)]}

    assert parse_scenes_response(payload) == [
        NdviScene(scene_date=date(2026, 9, 15), ndvi_mean=0.601, polygon_pixels=11, clear_fraction=1.0)
    ]


def test_a_fully_cloudy_pass_is_kept_with_no_ndvi():
    scenes = parse_scenes_response({"data": [_day("2026-09-17", 100, 89, 0.0, 0.0)]})

    assert scenes == [NdviScene(scene_date=date(2026, 9, 17), ndvi_mean=None, polygon_pixels=11, clear_fraction=0.0)]


def test_a_partly_cloudy_pass_averages_only_the_clear_pixels():
    # Half the farm is clear. The summed statistic is 0.3, so the clear half averages 0.6.
    scenes = parse_scenes_response({"data": [_day("2026-09-28", 100, 80, 0.5, 0.3)]})

    assert scenes[0].clear_fraction == 0.5
    assert scenes[0].ndvi_mean == 0.6
    assert scenes[0].polygon_pixels == 20


def test_a_day_with_no_pixels_inside_the_farm_is_skipped():
    assert parse_scenes_response({"data": [_day("2026-09-20", 100, 100, None, None)]}) == []


def test_an_empty_response_gives_no_scenes():
    assert parse_scenes_response({"data": []}) == []


def test_a_missing_statistic_never_produces_a_made_up_number():
    payload = {"data": [_day("2026-09-15", 100, 89, 1.0, None)]}

    assert parse_scenes_response(payload)[0].ndvi_mean is None


def test_the_cloud_classes_sent_to_copernicus_are_the_agreed_ones():
    assert CLOUDY_SCL_CLASSES == (0, 1, 3, 8, 9, 10)
    assert "[0, 1, 3, 8, 9, 10]" in SCENE_EVALSCRIPT


@requires_db
def test_scene_upsert_is_idempotent_and_a_later_run_corrects_a_day(db_session):
    from app.jobs.ingest_ndvi import _upsert_scenes
    from app.models.farm import Farm
    from app.models.farmer import Farmer
    from app.models.ndvi import FarmNdviScene

    farmer = Farmer(full_name="Test Farmer", phone="+2340000000001", state="Kano", lga="Kano Municipal")
    db_session.add(farmer)
    db_session.flush()
    farm = Farm(farmer_id=farmer.id, name="Test Farm", boundary_mapped=True)
    db_session.add(farm)
    db_session.flush()

    _upsert_scenes(db_session, farm, [NdviScene(date(2026, 9, 15), None, 11, 0.0)])
    _upsert_scenes(db_session, farm, [NdviScene(date(2026, 9, 15), 0.6, 11, 1.0), NdviScene(date(2026, 9, 17), None, 11, 0.0)])
    db_session.commit()

    rows = db_session.execute(
        select(FarmNdviScene).where(FarmNdviScene.farm_id == farm.id).order_by(FarmNdviScene.scene_date)
    ).scalars().all()
    assert [(r.scene_date, r.ndvi_mean, r.clear_fraction) for r in rows] == [
        (date(2026, 9, 15), 0.6, 1.0),
        (date(2026, 9, 17), None, 0.0),
    ]
