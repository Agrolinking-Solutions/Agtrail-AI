import hashlib
import hmac
import json
import logging
from urllib.parse import quote

import httpx
from geoalchemy2 import WKTElement

from app.core.config import settings

BOUNDARY_ENDPOINT = "/api/integrations/monitoring/farms/{farm_id}/boundary"
BOUNDARY_BATCH_ENDPOINT = "/api/integrations/monitoring/farms/boundaries"
BATCH_SIZE_LIMIT = 100

logger = logging.getLogger(__name__)


def _sign(raw_bytes: bytes) -> str:
    """
    Signs a payload the same way our own webhook signature check expects
    to see. Uses AGTRAIL_OUTBOUND_SECRET, which falls back to the webhook
    secret until AgTrail issues a separate key for this direction.
    """
    return hmac.new(
        key=settings.outbound_secret.encode("utf-8"),
        msg=raw_bytes,
        digestmod=hashlib.sha256,
    ).hexdigest()


def get_farm_boundary(farm_id: str) -> dict | None:
    """
    Fetches the mapped boundary for a single farm.

    The signature here is over the raw farm id string itself, not an
    empty body, this is different from every other signed request in
    this project, worth remembering if this ever needs debugging.

    Returns the parsed response data, or None if the farm id itself is
    invalid or not found. A farm that exists but has no polygon yet
    still returns normally, with mapped set to false, that's a real,
    expected state, not treated as an error here.
    """
    signature = _sign(farm_id.encode("utf-8"))
    # Quoted so a farm id containing / or ? can't change which endpoint
    # is called.
    url = f"{settings.AGTRAIL_API_BASE_URL}{BOUNDARY_ENDPOINT.format(farm_id=quote(farm_id, safe=''))}"

    try:
        response = httpx.get(
            url,
            headers={"X-AgTrail-Signature": signature},
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("Network error fetching boundary for %s: %s", farm_id, exc)
        return None

    if response.status_code == 404 or response.status_code == 400:
        logger.info("Farm %s not found or invalid: %s", farm_id, response.text[:500])
        return None

    if response.status_code == 401:
        logger.error("Signature rejected, check AGTRAIL_OUTBOUND_SECRET is current.")
        return None

    response.raise_for_status()
    body = response.json()
    return body.get("data")


def get_farm_boundaries_batch(farm_ids: list[str]) -> tuple[list[dict], list[str]]:
    """
    Fetches boundaries for up to 100 farms in one call. For more than
    100, call this multiple times with smaller batches, it does not
    chunk automatically, since the caller is better placed to decide
    how to split a larger list.

    Returns (items, missing_ids). Each item in items follows the same
    shape as get_farm_boundary's return value, including farms that
    exist but are not mapped yet.

    AgTrail rejects a whole batch when it holds a single id it cannot read
    (for example a test record such as "TEST-FARM-001"). A rejected batch is
    therefore split in half and tried again, down to the one bad id, so the
    other farms in it still get their boundaries and only the bad id is
    reported as missing.
    """
    if not farm_ids:
        return [], []

    if len(farm_ids) > BATCH_SIZE_LIMIT:
        raise ValueError(
            f"Batch size {len(farm_ids)} exceeds the cap of {BATCH_SIZE_LIMIT}, split into smaller batches first."
        )

    items: list[dict] = []
    missing: list[str] = []
    _fetch_batch(farm_ids, items, missing)
    return items, missing


def _fetch_batch(farm_ids: list[str], items: list[dict], missing: list[str]) -> None:
    # Signed over the exact bytes on the wire, compact separators,
    # matching Victor's own example exactly, a pretty printed or
    # differently spaced JSON body will fail the signature check.
    body_str = json.dumps({"farmIds": farm_ids}, separators=(",", ":"))
    body_bytes = body_str.encode("utf-8")
    signature = _sign(body_bytes)

    url = f"{settings.AGTRAIL_API_BASE_URL}{BOUNDARY_BATCH_ENDPOINT}"

    try:
        response = httpx.post(
            url,
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-AgTrail-Signature": signature,
            },
            timeout=60.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("Network error fetching boundary batch: %s", exc)
        missing.extend(farm_ids)
        return

    if response.status_code == 400:
        if len(farm_ids) == 1:
            logger.warning("AgTrail rejected farm id %r: %s", farm_ids[0], response.text[:300])
            missing.extend(farm_ids)
            return
        logger.warning(
            "Batch of %d rejected (%s), splitting it to find the bad id.", len(farm_ids), response.text[:200]
        )
        middle = len(farm_ids) // 2
        _fetch_batch(farm_ids[:middle], items, missing)
        _fetch_batch(farm_ids[middle:], items, missing)
        return

    response.raise_for_status()
    data = response.json().get("data", {})
    items.extend(data.get("items", []))
    missing.extend(data.get("missingIds", []))


def geojson_polygon_to_wkt(geometry: dict) -> WKTElement:
    """
    Converts the GeoJSON Polygon this API returns (coordinates as
    [lng, lat] pairs, exterior ring first) into the WKTElement our
    Farm.boundary column expects. Interior rings (holes) are dropped, a
    farm boundary with an actual hole cut out of it would be unusual and
    isn't something we currently have a use for.
    """
    if geometry.get("type") != "Polygon":
        raise ValueError(f"Expected a GeoJSON Polygon, got {geometry.get('type')!r}")

    rings = geometry.get("coordinates") or []
    if not rings:
        raise ValueError("Polygon geometry has no coordinates")
    if len(rings) > 1:
        logger.warning("Dropping %d interior ring(s) from a farm boundary polygon.", len(rings) - 1)

    exterior = rings[0]
    points = ", ".join(f"{lng} {lat}" for lng, lat in exterior)
    return WKTElement(f"POLYGON(({points}))", srid=4326)