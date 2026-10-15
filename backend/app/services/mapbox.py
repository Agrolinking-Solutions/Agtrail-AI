"""
A second satellite imagery source alongside Copernicus. Mapbox serves
high-resolution commercial imagery (Maxar, in most areas) through its
Static Images API, noticeably sharper than Sentinel-2's 10m pixels for
the same farm. Unlike the Sentinel styles, this doesn't depend on an
NDVI observation existing, it only needs the farm's boundary, since
there's no time window to pick, just the current imagery.
"""

import json
import logging
from urllib.parse import quote

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

STATIC_IMAGE_URL = "https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static"
IMAGE_SIZE_PX = 700
PADDING_PX = 60


def fetch_boundary_satellite_image(boundary_geojson: dict) -> bytes:
    feature = {
        "type": "Feature",
        "properties": {
            "stroke": "#19462d",
            "stroke-width": 3,
            "fill": "#2f8c5a",
            "fill-opacity": 0.35,
        },
        "geometry": boundary_geojson,
    }
    encoded = quote(json.dumps(feature, separators=(",", ":")))
    url = f"{STATIC_IMAGE_URL}/geojson({encoded})/auto/{IMAGE_SIZE_PX}x{IMAGE_SIZE_PX}@2x"

    response = httpx.get(
        url,
        params={"access_token": settings.MAPBOX_ACCESS_TOKEN, "padding": str(PADDING_PX)},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.content
