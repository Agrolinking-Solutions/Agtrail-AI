"""
Sentinel-2 NDVI per farm via the Copernicus Data Space Statistics API.

The API computes the mean NDVI over each farm polygon for each time interval
on the server side, so we never download raw imagery. Farms are small (often
only 10 to 30 Sentinel-2 pixels), so a per-farm mean is what we actually need.
"""

import json
import logging
import time
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

TRUE_COLOR_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B02", "B03", "B04", "dataMask"] }],
    output: { bands: 4 }
  };
}

// Sentinel-2 L2A bands come back as reflectance, 0 to 1. Raw reflectance
// reads dark on screen, so a gain brightens it; clamping afterward keeps
// bright soil or rooftops from blowing out past white.
var GAIN = 7.0;
function clamp01(x) {
  return Math.min(Math.max(x, 0), 1);
}

function evaluatePixel(s) {
  return [clamp01(s.B04 * GAIN), clamp01(s.B03 * GAIN), clamp01(s.B02 * GAIN), s.dataMask];
}
"""

# Sentinel-2 scene classification (SCL) classes that must not count as crop:
# 0 no data, 1 saturated, 3 cloud shadow, 8 and 9 cloud (medium, high
# probability), 10 thin cirrus. Judged per pixel, because a scene that is 25%
# cloudy overall can still be completely clear over one small farm, or the
# reverse.
CLOUDY_SCL_CLASSES = (0, 1, 3, 8, 9, 10)
# Roughly 10 m, Sentinel-2's pixel size, in degrees. Without an explicit
# resolution the API stretches the farm's bounding box onto a fixed 256 x 256
# grid, which makes every pixel count meaningless.
SCENE_RESOLUTION_DEG = 0.00009
SCENE_EPSG4326 = "http://www.opengis.net/def/crs/EPSG/0/4326"

# Two statistics are asked for so one request gives both answers exactly:
# "clear" (1 or 0 per pixel) averages to the clear fraction of the farm, and
# "ndvi_clear" (NDVI on clear pixels, 0 elsewhere) divided by that fraction is
# the mean NDVI over the clear pixels only.
SCENE_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B04", "B08", "SCL", "dataMask"] }],
    output: [
      { id: "ndvi_clear", bands: 1 },
      { id: "clear", bands: 1 },
      { id: "dataMask", bands: 1 }
    ]
  };
}
function evaluatePixel(s) {
  var cloudy = %s;
  var clear = cloudy.indexOf(s.SCL) === -1 ? 1 : 0;
  var ndvi = (s.B08 - s.B04) / (s.B08 + s.B04);
  return { ndvi_clear: [clear ? ndvi : 0], clear: [clear], dataMask: [s.dataMask] };
}
""" % list(CLOUDY_SCL_CLASSES)

IMAGE_STYLES = {"ndvi": NDVI_IMAGE_EVALSCRIPT, "true-color": TRUE_COLOR_EVALSCRIPT}


@dataclass(frozen=True)
class NdviPeriod:
    period_start: date
    period_end: date
    ndvi_mean: float
    valid_pixel_count: int


@dataclass(frozen=True)
class NdviScene:
    """One satellite pass over one farm. ndvi_mean is None when no pixel of
    the farm was clear that day (the farm was under cloud)."""

    scene_date: date
    ndvi_mean: float | None
    polygon_pixels: int
    clear_fraction: float


# Copernicus limits how fast one client may ask, and answers 429 when it is asked too fast.
# That, a server error or a dropped connection is waited out and tried again; anything else
# (a bad request, a missing farm) is a real answer and is raised at once.
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 5
MAX_WAIT_SECONDS = 60.0
_sleep = time.sleep


def _wait_seconds(response: httpx.Response | None, attempt: int) -> float:
    """Copernicus's own Retry-After when it sends one, otherwise 2, 4, 8, 16 seconds."""
    if response is not None:
        try:
            return min(float(response.headers.get("Retry-After", "")), MAX_WAIT_SECONDS)
        except ValueError:
            pass
    return min(2.0 * (2 ** (attempt - 1)), MAX_WAIT_SECONDS)


def _post_with_retries(url: str, *, max_attempts: int = MAX_ATTEMPTS, **kwargs) -> httpx.Response:
    for attempt in range(1, max_attempts + 1):
        response = None
        try:
            response = httpx.post(url, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt == max_attempts:
                raise
            logger.warning("Copernicus request failed (%s), attempt %d of %d.", type(exc).__name__, attempt, max_attempts)
        else:
            if response.status_code not in RETRY_STATUSES or attempt == max_attempts:
                return response
            logger.warning("Copernicus answered %d, attempt %d of %d.", response.status_code, attempt, max_attempts)
        _sleep(_wait_seconds(response, attempt))
    raise AssertionError("unreachable")


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

    response = _post_with_retries(
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


def fetch_farm_scenes(boundary_geojson: dict, start: date, end: date, token: str) -> list[NdviScene]:
    """Every pass in the window, one entry per day with imagery, with clouds
    removed pixel by pixel. Passes that were fully cloudy are returned too
    (ndvi_mean None), so the record shows the farm was looked at and not
    visible, rather than leaving a silent gap."""
    time_range = {
        "from": datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).isoformat(),
        "to": datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc).isoformat(),
    }
    body = {
        "input": {
            "bounds": {"geometry": boundary_geojson, "properties": {"crs": SCENE_EPSG4326}},
            # No image-wide cloud filter: that would drop a pass that is clear over this farm.
            "data": [{"type": "sentinel-2-l2a", "dataFilter": {"timeRange": time_range}}],
        },
        "aggregation": {
            "timeRange": time_range,
            "aggregationInterval": {"of": "P1D"},
            "evalscript": SCENE_EVALSCRIPT,
            "resx": SCENE_RESOLUTION_DEG,
            "resy": SCENE_RESOLUTION_DEG,
        },
        "calculations": {"default": {}},
    }
    response = _post_with_retries(
        STATISTICS_URL,
        content=json.dumps(body),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=120.0,
    )
    response.raise_for_status()
    return parse_scenes_response(response.json())


def parse_scenes_response(payload: dict) -> list[NdviScene]:
    scenes = []
    for item in payload.get("data", []):
        outputs = item["outputs"]
        clear_stats = outputs["clear"]["bands"]["B0"]["stats"]
        polygon_pixels = (clear_stats.get("sampleCount") or 0) - (clear_stats.get("noDataCount") or 0)
        if polygon_pixels <= 0:
            continue
        clear_fraction = min(max(float(clear_stats.get("mean") or 0.0), 0.0), 1.0)
        ndvi_mean = None
        if clear_fraction > 0:
            ndvi_sum = outputs["ndvi_clear"]["bands"]["B0"]["stats"].get("mean")
            if ndvi_sum is not None:
                ndvi_mean = float(ndvi_sum) / clear_fraction
        scenes.append(
            NdviScene(
                scene_date=datetime.fromisoformat(item["interval"]["from"].replace("Z", "+00:00")).date(),
                ndvi_mean=ndvi_mean,
                polygon_pixels=int(polygon_pixels),
                clear_fraction=clear_fraction,
            )
        )
    return scenes


def fetch_farm_image(
    evalscript: str,
    boundary_geojson: dict,
    start: date,
    end: date,
    token: str,
) -> bytes:
    """Renders a single PNG, the clearest composite Sentinel-2 can build
    from the window, masked outside the farm boundary via dataMask as
    alpha. The evalscript picks the style, NDVI health or true color."""
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
        "evalscript": evalscript,
    }

    response = _post_with_retries(
        PROCESS_URL,
        max_attempts=2,  # a person is waiting for the picture
        content=json.dumps(body),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "image/png"},
        timeout=120.0,
    )
    response.raise_for_status()
    return response.content


def default_window(days: int = 90) -> tuple[date, date]:
    end = datetime.now(timezone.utc).date()
    return end - timedelta(days=days), end
