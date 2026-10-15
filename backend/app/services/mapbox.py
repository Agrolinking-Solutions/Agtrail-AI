"""
A second satellite imagery source alongside Copernicus. Mapbox serves
high-resolution commercial imagery (Maxar, in most areas) through its
Static Images API, noticeably sharper than Sentinel-2's 10m pixels for
the same farm. Unlike the Sentinel styles, these don't depend on an
NDVI observation existing, they only need the farm's boundary, since
there's no time window to pick, just the current imagery.

Two different shots, for two different questions:
- fetch_boundary_satellite_image: tightly auto-framed on the farm
  itself, "what does this exact plot look like."
- fetch_area_view: a fixed, wider zoom around the farm's center, on
  Mapbox's drawn street map, "where is this plot, what's around it."
  Auto-framing a plot this small would zoom in too tight to show any
  of the surrounding roads or places even when they exist nearby.
"""

import json
import logging
from urllib.parse import quote

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

BASE_STYLE_URL = "https://api.mapbox.com/styles/v1/mapbox/{style}/static"
IMAGE_SIZE_PX = 700
AREA_VIEW_SIZE_PX = 800
PADDING_PX = 60
AREA_VIEW_ZOOM = 15

_BOUNDARY_STYLE_PROPERTIES = {
    "stroke": "#19462d",
    "stroke-width": 3,
    "fill": "#2f8c5a",
    "fill-opacity": 0.35,
}


def _boundary_overlay(boundary_geojson: dict) -> str:
    feature = {
        "type": "Feature",
        "properties": _BOUNDARY_STYLE_PROPERTIES,
        "geometry": boundary_geojson,
    }
    return quote(json.dumps(feature, separators=(",", ":")))


def fetch_boundary_satellite_image(boundary_geojson: dict) -> bytes:
    url_base = BASE_STYLE_URL.format(style="satellite-v9")
    overlay = _boundary_overlay(boundary_geojson)
    url = f"{url_base}/geojson({overlay})/auto/{IMAGE_SIZE_PX}x{IMAGE_SIZE_PX}@2x"

    response = httpx.get(
        url,
        params={"access_token": settings.MAPBOX_ACCESS_TOKEN, "padding": str(PADDING_PX)},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.content


def fetch_area_view(longitude: float, latitude: float, boundary_geojson: dict) -> bytes:
    url_base = BASE_STYLE_URL.format(style="streets-v12")
    overlay = _boundary_overlay(boundary_geojson)
    position = f"{longitude},{latitude},{AREA_VIEW_ZOOM},0,0"
    url = f"{url_base}/geojson({overlay})/{position}/{AREA_VIEW_SIZE_PX}x{AREA_VIEW_SIZE_PX}@2x"

    response = httpx.get(url, params={"access_token": settings.MAPBOX_ACCESS_TOKEN}, timeout=30.0)
    response.raise_for_status()
    return response.content
