"""Tests for the Mapbox satellite image service, the HTTP call mocked."""

import httpx

from app.services.mapbox import fetch_boundary_satellite_image

FAKE_PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes"
BOUNDARY = {"type": "Polygon", "coordinates": [[[7.9, 4.7], [7.99, 4.7], [7.99, 4.75], [7.9, 4.75], [7.9, 4.7]]]}


def test_fetch_boundary_satellite_image_builds_a_geojson_overlay_request(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return httpx.Response(200, content=FAKE_PNG, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)

    result = fetch_boundary_satellite_image(BOUNDARY)

    assert result == FAKE_PNG
    assert "/auto/" in captured["url"]
    assert "geojson(" in captured["url"]
    assert "access_token" in captured["params"]


def test_fetch_boundary_satellite_image_raises_on_http_error(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return httpx.Response(401, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)

    try:
        fetch_boundary_satellite_image(BOUNDARY)
        assert False, "expected an HTTPStatusError"
    except httpx.HTTPStatusError:
        pass
