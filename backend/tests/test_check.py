"""Tests for the public /check endpoints, for a plot that isn't necessarily an AgTrail farm."""

from datetime import date

import pytest

from tests.conftest import API_KEY, requires_db

pytestmark = requires_db

FAKE_PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes"

# Roughly 123 ha, a sane size for a single plot, well within the allowed range.
VALID_BOUNDARY = {
    "type": "Polygon",
    "coordinates": [[[7.90, 4.70], [7.91, 4.70], [7.91, 4.71], [7.90, 4.71], [7.90, 4.70]]],
}

# A sliver with near-zero area, below the minimum sane plot size.
TOO_SMALL_BOUNDARY = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [0.00001, 0], [0.00001, 0.00001], [0, 0.00001], [0, 0]]],
}

# A 10x10 degree box, orders of magnitude past any single plot.
TOO_LARGE_BOUNDARY = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
}


def test_check_returns_area_centroid_and_ndvi(client, monkeypatch):
    import app.api.routes.check as check_module
    from app.services.sentinel import NdviPeriod

    monkeypatch.setattr(check_module, "get_access_token", lambda: "token")
    monkeypatch.setattr(
        check_module, "fetch_farm_ndvi",
        lambda *a, **k: [NdviPeriod(date(2026, 9, 1), date(2026, 9, 11), 0.55, 200)],
    )

    response = client.post(
        "/check", json={"boundary": VALID_BOUNDARY}, headers={"X-API-Key": API_KEY}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["area_hectares"] > 0
    assert body["centroid"]["latitude"] == pytest.approx(4.705)
    assert body["centroid"]["longitude"] == pytest.approx(7.905)
    assert body["ndvi"] == {
        "period_start": "2026-09-01", "period_end": "2026-09-11",
        "ndvi_mean": 0.55, "valid_pixel_count": 200,
    }


def test_check_ndvi_is_null_when_no_clear_periods(client, monkeypatch):
    import app.api.routes.check as check_module

    monkeypatch.setattr(check_module, "get_access_token", lambda: "token")
    monkeypatch.setattr(check_module, "fetch_farm_ndvi", lambda *a, **k: [])

    response = client.post(
        "/check", json={"boundary": VALID_BOUNDARY}, headers={"X-API-Key": API_KEY}
    )

    assert response.status_code == 200
    assert response.json()["ndvi"] is None


def test_check_rejects_malformed_geojson(client):
    response = client.post(
        "/check", json={"boundary": {"type": "NotAGeometry"}}, headers={"X-API-Key": API_KEY}
    )

    assert response.status_code == 422


def test_check_rejects_too_small_area(client):
    response = client.post(
        "/check", json={"boundary": TOO_SMALL_BOUNDARY}, headers={"X-API-Key": API_KEY}
    )

    assert response.status_code == 422
    assert "too small" in response.json()["detail"]


def test_check_rejects_too_large_area(client):
    response = client.post(
        "/check", json={"boundary": TOO_LARGE_BOUNDARY}, headers={"X-API-Key": API_KEY}
    )

    assert response.status_code == 422
    assert "implausibly large" in response.json()["detail"]


def test_check_requires_api_key(client):
    response = client.post("/check", json={"boundary": VALID_BOUNDARY})

    assert response.status_code == 401


def test_check_image_mapbox_style(client, monkeypatch):
    import app.api.routes.check as check_module

    monkeypatch.setattr(check_module, "fetch_boundary_satellite_image", lambda *a, **k: FAKE_PNG)

    response = client.post(
        "/check/image?style=mapbox", json={"boundary": VALID_BOUNDARY}, headers={"X-API-Key": API_KEY}
    )

    assert response.status_code == 200
    assert response.content == FAKE_PNG


def test_check_image_mapbox_area_style(client, monkeypatch):
    import app.api.routes.check as check_module

    captured = {}
    monkeypatch.setattr(
        check_module, "fetch_area_view",
        lambda lng, lat, boundary: captured.update(lng=lng, lat=lat) or FAKE_PNG,
    )

    response = client.post(
        "/check/image?style=mapbox-area", json={"boundary": VALID_BOUNDARY}, headers={"X-API-Key": API_KEY}
    )

    assert response.status_code == 200
    assert response.content == FAKE_PNG
    assert captured["lng"] == pytest.approx(7.905)
    assert captured["lat"] == pytest.approx(4.705)


def test_check_image_ndvi_style(client, monkeypatch):
    import app.api.routes.check as check_module

    monkeypatch.setattr(check_module, "get_access_token", lambda: "token")
    monkeypatch.setattr(check_module, "fetch_farm_image", lambda *a, **k: FAKE_PNG)

    response = client.post(
        "/check/image?style=ndvi", json={"boundary": VALID_BOUNDARY}, headers={"X-API-Key": API_KEY}
    )

    assert response.status_code == 200
    assert response.content == FAKE_PNG


def test_check_image_rejects_unknown_style(client):
    response = client.post(
        "/check/image?style=bogus", json={"boundary": VALID_BOUNDARY}, headers={"X-API-Key": API_KEY}
    )

    assert response.status_code == 422


def test_check_image_requires_api_key(client):
    response = client.post("/check/image", json={"boundary": VALID_BOUNDARY})

    assert response.status_code == 401
