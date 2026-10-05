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
INTERVAL = "P10D"
MAX_CLOUD_COVERAGE = 30

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


def default_window(days: int = 90) -> tuple[date, date]:
    end = datetime.now(timezone.utc).date()
    return end - timedelta(days=days), end
