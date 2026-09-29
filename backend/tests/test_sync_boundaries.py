"""Tests for the boundary sync job, against a real PostGIS database."""

import uuid

from sqlalchemy import func, select

from tests.conftest import requires_db

pytestmark = requires_db

MAPPED_ITEM = {
    "farmId": "AGT-1",
    "mapped": True,
    "updatedAt": "2026-08-01T00:00:00.000Z",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[7.9, 4.7], [7.99, 4.7], [7.99, 4.75], [7.9, 4.75], [7.9, 4.7]]],
    },
    "areaHectares": 0.33,
}

UNMAPPED_ITEM = {
    "farmId": "AGT-2",
    "mapped": False,
    "updatedAt": "2026-08-01T00:00:00.000Z",
    "geometry": None,
    "areaHectares": 0.12,
}


def _make_farm(db_session, agtrail_farm_id: str | None) -> "Farm":
    from app.models.farm import Farm
    from app.models.farmer import Farmer

    farmer = Farmer(full_name="Test Farmer", phone="080000000", state="Oyo", lga="Ibadan North")
    db_session.add(farmer)
    db_session.flush()

    farm = Farm(farmer_id=farmer.id, name="Test Farm", agtrail_farm_id=agtrail_farm_id)
    db_session.add(farm)
    db_session.commit()
    return farm


def test_farms_without_agtrail_id_are_skipped(db_session, monkeypatch):
    from app.jobs.sync_boundaries import sync_boundaries

    _make_farm(db_session, agtrail_farm_id=None)
    called = []
    monkeypatch.setattr(
        "app.jobs.sync_boundaries.get_farm_boundaries_batch",
        lambda ids: called.append(ids) or ([], []),
    )

    result = sync_boundaries()

    assert result["considered"] == 0
    assert called == []


def test_mapped_farm_gets_a_usable_boundary(db_session, monkeypatch):
    from app.jobs.sync_boundaries import sync_boundaries

    farm = _make_farm(db_session, agtrail_farm_id="AGT-1")
    monkeypatch.setattr(
        "app.jobs.sync_boundaries.get_farm_boundaries_batch",
        lambda ids: ([MAPPED_ITEM], []),
    )

    result = sync_boundaries()

    assert result == {"considered": 1, "updated": 1, "unchanged": 0, "missing": 0}
    db_session.refresh(farm)
    assert farm.boundary_mapped is True
    assert farm.boundary is not None

    # Prove the WKT conversion produced a real, usable polygon, not just
    # a non-null value: a point inside it should test as covered.
    covers = db_session.scalar(
        select(func.ST_Covers(farm.boundary, func.ST_SetSRID(func.ST_MakePoint(7.95, 4.72), 4326)))
    )
    assert covers is True


def test_unmapped_farm_gets_no_boundary(db_session, monkeypatch):
    from app.jobs.sync_boundaries import sync_boundaries

    farm = _make_farm(db_session, agtrail_farm_id="AGT-2")
    monkeypatch.setattr(
        "app.jobs.sync_boundaries.get_farm_boundaries_batch",
        lambda ids: ([UNMAPPED_ITEM], []),
    )

    sync_boundaries()

    db_session.refresh(farm)
    assert farm.boundary_mapped is False
    assert farm.boundary is None


def test_unchanged_updated_at_is_not_rewritten(db_session, monkeypatch):
    from app.jobs.sync_boundaries import sync_boundaries

    farm = _make_farm(db_session, agtrail_farm_id="AGT-1")
    monkeypatch.setattr(
        "app.jobs.sync_boundaries.get_farm_boundaries_batch",
        lambda ids: ([MAPPED_ITEM], []),
    )
    sync_boundaries()
    db_session.refresh(farm)
    original_boundary_wkt = db_session.scalar(select(func.ST_AsText(farm.boundary)))

    # Same updatedAt, but a different (wrong) geometry — proves this
    # second sync is skipped rather than overwriting with it.
    changed_geometry_same_timestamp = {
        **MAPPED_ITEM,
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
        },
    }
    monkeypatch.setattr(
        "app.jobs.sync_boundaries.get_farm_boundaries_batch",
        lambda ids: ([changed_geometry_same_timestamp], []),
    )

    result = sync_boundaries()

    assert result["unchanged"] == 1
    assert result["updated"] == 0
    db_session.refresh(farm)
    assert db_session.scalar(select(func.ST_AsText(farm.boundary))) == original_boundary_wkt


def test_missing_ids_are_counted_not_fatal(db_session, monkeypatch):
    from app.jobs.sync_boundaries import sync_boundaries

    _make_farm(db_session, agtrail_farm_id="AGT-1")
    monkeypatch.setattr(
        "app.jobs.sync_boundaries.get_farm_boundaries_batch",
        lambda ids: ([], ids),
    )

    result = sync_boundaries()

    assert result["missing"] == 1
    assert result["updated"] == 0


def test_farms_are_split_into_batches(db_session, monkeypatch):
    from app.jobs.sync_boundaries import sync_boundaries

    for i in range(3):
        _make_farm(db_session, agtrail_farm_id=f"AGT-{i}")

    monkeypatch.setattr("app.jobs.sync_boundaries.BATCH_SIZE_LIMIT", 2)
    calls = []

    def fake_batch(ids):
        calls.append(ids)
        return [], []

    monkeypatch.setattr("app.jobs.sync_boundaries.get_farm_boundaries_batch", fake_batch)

    sync_boundaries()

    assert len(calls) == 2
    assert sorted(len(c) for c in calls) == [1, 2]
    assert sorted(id for batch in calls for id in batch) == ["AGT-0", "AGT-1", "AGT-2"]
