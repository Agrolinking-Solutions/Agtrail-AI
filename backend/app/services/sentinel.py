"""
Sentinel-2 NDVI per farm via the Copernicus Data Space Statistics API.

The API computes the mean NDVI over each farm polygon for each time interval
on the server side, so we never download raw imagery. Farms are small (often
only 10 to 30 Sentinel-2 pixels), so a per-farm mean is what we actually need.
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
STATISTICS_URL = "https://sh.dataspace.copernicus.eu/api/v1/statistics"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"
INTERVAL = "P10D"
MAX_CLOUD_COVERAGE = 30
IMAGE_SIZE_PX = 512

NDVI_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B04", "B08", "dataMask"] }],
    output: [
      { id: "ndvi", bands: 1 },
      { id: "dataMask", bands: 1 }
    ]
  };
}
function evaluatePixel(s) {
  let ndvi = (s.B08 - s.B04) / (s.B08 + s.B04);
  return { ndvi: [ndvi], dataMask: [s.dataMask] };
}
"""


NDVI_IMAGE_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B04", "B08", "dataMask"] }],
    output: { bands: 4 }
  };
}

// Brown (bare soil / low vegetation) to green (dense vegetation), the same
// convention most agricultural NDVI maps use, so the image reads the same
// way a farmer or agent would expect.
var STOPS = [
  [-1.0, [120, 90, 60]],
  [0.0,  [170, 140, 90]],
  [0.2,  [230, 220, 140]],
  [0.4,  [170, 210, 90]],
  [0.6,  [90, 170, 60]],
  [1.0,  [20, 100, 30]]
];

function colorFor(ndvi) {
  for (var i = 1; i < STOPS.length; i++) {
    var lo = STOPS[i - 1], hi = STOPS[i];
    if (ndvi <= hi[0]) {
      var t = (ndvi - lo[0]) / (hi[0] - lo[0]);
      return [
        (lo[1][0] + t * (hi[1][0] - lo[1][0])) / 255,
        (lo[1][1] + t * (hi[1][1] - lo[1][1])) / 255,
        (lo[1][2] + t * (hi[1][2] - lo[1][2])) / 255,
      ];
    }
  }
  return [STOPS[STOPS.length - 1][1][0] / 255, STOPS[STOPS.length - 1][1][1] / 255, STOPS[STOPS.length - 1][1][2] / 255];
}

function evaluatePixel(s) {
  let ndvi = (s.B08 - s.B04) / (s.B08 + s.B04);
  let rgb = colorFor(ndvi);
  return [rgb[0], rgb[1], rgb[2], s.dataMask];
}
"""


@dataclass(frozen=True)
class NdviPeriod:
    period_start: date
    period_end: date
    ndvi_mean: float
    valid_pixel_count: int


def get_access_token() -> str:
    response = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": settings.CDSE_CLIENT_ID,
            "client_secret": settings.CDSE_CLIENT_SECRET,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def fetch_farm_ndvi(
    boundary_geojson: dict,
    start: date,
    end: date,
    token: str,
) -> list[NdviPeriod]:
    time_range = {
        "from": datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).isoformat(),
        "to": datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc).isoformat(),
    }
    body = {
        "input": {
            "bounds": {"geometry": boundary_geojson},
            "data": [
                {
                    "type": "sentinel-2-l2a",
                    "dataFilter": {"timeRange": time_range, "maxCloudCoverage": MAX_CLOUD_COVERAGE},
                }
            ],
        },
        "aggregation": {
            "timeRange": time_range,
            "aggregationInterval": {"of": INTERVAL},
            "evalscript": NDVI_EVALSCRIPT,
        },
        "calculations": {"ndvi": {}},
    }

    response = httpx.post(
        STATISTICS_URL,
        content=json.dumps(body),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=120.0,
    )
    response.raise_for_status()
    return parse_statistics_response(response.json())


def parse_statistics_response(payload: dict) -> list[NdviPeriod]:
    periods = []
    for item in payload.get("data", []):
        stats = item["outputs"]["ndvi"]["bands"]["B0"]["stats"]
        mean = stats.get("mean")
        sample_count = stats.get("sampleCount") or 0
        if mean is None or sample_count == 0:
            continue
        interval = item["interval"]
        periods.append(
            NdviPeriod(
                period_start=datetime.fromisoformat(interval["from"].replace("Z", "+00:00")).date(),
                period_end=datetime.fromisoformat(interval["to"].replace("Z", "+00:00")).date(),
                ndvi_mean=float(mean),
                valid_pixel_count=int(sample_count),
            )
        )
    return periods


def fetch_farm_ndvi_image(
    boundary_geojson: dict,
    start: date,
    end: date,
    token: str,
) -> bytes:
    """Renders a single PNG, the clearest composite Sentinel-2 can build
    from the window, colored brown-to-green by NDVI. Masked outside the
    farm boundary via dataMask as alpha."""
    time_range = {
        "from": datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).isoformat(),
        "to": datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc).isoformat(),
    }
    body = {
        "input": {
            "bounds": {"geometry": boundary_geojson},
            "data": [
                {
                    "type": "sentinel-2-l2a",
                    "dataFilter": {"timeRange": time_range, "maxCloudCoverage": MAX_CLOUD_COVERAGE, "mosaickingOrder": "leastCC"},
                }
            ],
        },
        "output": {"width": IMAGE_SIZE_PX, "height": IMAGE_SIZE_PX, "responses": [{"identifier": "default", "format": {"type": "image/png"}}]},
        "evalscript": NDVI_IMAGE_EVALSCRIPT,
    }

    response = httpx.post(
        PROCESS_URL,
        content=json.dumps(body),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "image/png"},
        timeout=120.0,
    )
    response.raise_for_status()
    return response.content


def default_window(days: int = 90) -> tuple[date, date]:
    end = datetime.now(timezone.utc).date()
    return end - timedelta(days=days), end
