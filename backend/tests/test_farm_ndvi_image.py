"""Tests for the farm NDVI image endpoint, with the Copernicus calls mocked."""

from datetime import date

from geoalchemy2 import WKTElement

from tests.conftest import API_KEY, requires_db

pytestmark = requires_db

FAKE_PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes"


def _make_farm_with_boundary(db_session):
    from app.models.farm import Farm
    from app.models.farmer import Farmer

    farmer = Farmer(full_name="Test Farmer", phone="080000000", state="Oyo", lga="Ibadan North")
    db_session.add(farmer)
    db_session.flush()
    farm = Farm(
        farmer_id=farmer.id,
        name="Test Farm",
        boundary_mapped=True,
        boundary=WKTElement("POLYGON((7.9 4.7, 7.99 4.7, 7.99 4.75, 7.9 4.75, 7.9 4.7))", srid=4326),
    )
    db_session.add(farm)
    db_session.flush()
    return farm


def _add_observation(db_session, farm, start: date, end: date):
    from app.models.ndvi import FarmNdviObservation

    db_session.add(
        FarmNdviObservation(
            farm_id=farm.id, period_start=start, period_end=end,
            ndvi_mean=0.5, valid_pixel_count=100,
        )
    )


def test_ndvi_image_renders_and_is_cached_on_second_call(client, db_session, monkeypatch):
    import app.api.routes.farms as farms_module

    farm = _make_farm_with_boundary(db_session)
    _add_observation(db_session, farm, date(2026, 9, 1), date(2026, 9, 11))
    db_session.commit()

    farms_module._IMAGE_CACHE.clear()
    calls = []
    monkeypatch.setattr(farms_module, "get_access_token", lambda: "token")
    monkeypatch.setattr(
        farms_module, "fetch_farm_ndvi_image",
        lambda *a, **k: calls.append(1) or FAKE_PNG,
    )

    first = client.get(f"/farms/{farm.id}/ndvi-image", headers={"X-API-Key": API_KEY})
    second = client.get(f"/farms/{farm.id}/ndvi-image", headers={"X-API-Key": API_KEY})

    assert first.status_code == 200
    assert first.headers["content-type"] == "image/png"
    assert first.content == FAKE_PNG
    assert second.content == FAKE_PNG
    assert len(calls) == 1, "second request should be served from cache, not a new render"


def test_ndvi_image_404_for_farm_with_no_observations(client, db_session):
    farm = _make_farm_with_boundary(db_session)
    db_session.commit()

    response = client.get(f"/farms/{farm.id}/ndvi-image", headers={"X-API-Key": API_KEY})

    assert response.status_code == 404


def test_ndvi_image_404_for_unknown_farm(client):
    import uuid

    response = client.get(f"/farms/{uuid.uuid4()}/ndvi-image", headers={"X-API-Key": API_KEY})

    assert response.status_code == 404


def test_ndvi_image_requires_api_key(client, db_session):
    farm = _make_farm_with_boundary(db_session)
    db_session.commit()

    response = client.get(f"/farms/{farm.id}/ndvi-image")

    assert response.status_code == 401
