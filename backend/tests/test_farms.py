"""Tests for the farm NDVI trend endpoint."""

from datetime import date

from tests.conftest import API_KEY, requires_db

pytestmark = requires_db


def _make_farm(db_session):
    from app.models.farm import Farm
    from app.models.farmer import Farmer

    farmer = Farmer(full_name="Test Farmer", phone="080000000", state="Oyo", lga="Ibadan North")
    db_session.add(farmer)
    db_session.flush()
    farm = Farm(farmer_id=farmer.id, name="Test Farm")
    db_session.add(farm)
    db_session.flush()
    return farm


def _add_observation(db_session, farm, start: date, end: date, ndvi: float):
    from app.models.ndvi import FarmNdviObservation

    db_session.add(
        FarmNdviObservation(
            farm_id=farm.id, period_start=start, period_end=end,
            ndvi_mean=ndvi, valid_pixel_count=100,
        )
    )


def test_ndvi_trend_returns_oldest_to_newest(client, db_session):
    farm = _make_farm(db_session)
    _add_observation(db_session, farm, date(2026, 8, 14), date(2026, 8, 24), 0.4)
    _add_observation(db_session, farm, date(2026, 7, 25), date(2026, 8, 4), 0.3)
    _add_observation(db_session, farm, date(2026, 9, 3), date(2026, 9, 13), 0.5)
    db_session.commit()

    response = client.get(f"/farms/{farm.id}/ndvi", headers={"X-API-Key": API_KEY})

    assert response.status_code == 200
    body = response.json()
    assert [row["period_start"] for row in body] == ["2026-07-25", "2026-08-14", "2026-09-03"]
    assert [row["ndvi_mean"] for row in body] == [0.3, 0.4, 0.5]


def test_ndvi_trend_respects_limit(client, db_session):
    farm = _make_farm(db_session)
    for i in range(5):
        _add_observation(db_session, farm, date(2026, 7, 1 + i), date(2026, 7, 10 + i), 0.1 * i)
    db_session.commit()

    response = client.get(f"/farms/{farm.id}/ndvi?limit=2", headers={"X-API-Key": API_KEY})

    assert response.status_code == 200
    assert len(response.json()) == 2


def test_ndvi_trend_404_for_unknown_farm(client):
    import uuid

    response = client.get(f"/farms/{uuid.uuid4()}/ndvi", headers={"X-API-Key": API_KEY})

    assert response.status_code == 404


def test_ndvi_trend_empty_for_farm_with_no_observations(client, db_session):
    farm = _make_farm(db_session)
    db_session.commit()

    response = client.get(f"/farms/{farm.id}/ndvi", headers={"X-API-Key": API_KEY})

    assert response.status_code == 200
    assert response.json() == []


def test_ndvi_trend_requires_api_key(client, db_session):
    farm = _make_farm(db_session)
    db_session.commit()

    response = client.get(f"/farms/{farm.id}/ndvi")

    assert response.status_code == 401
