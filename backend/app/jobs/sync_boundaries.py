"""
Pulls the mapped boundary polygon for every farm from AgTrail and stores
it on farms.boundary. Nothing else in the app currently calls the
boundary client (see app/services/agtrail_boundary_client.py) — without
this job, farms.boundary_mapped stays false forever, which means the
GPS-verification check on incoming photos never has anything to check
against.

Run monthly, matching AgTrail's own suggested cadence and how often
farm boundaries actually change:
    python -m app.jobs.sync_boundaries

Usage from Makefile: make sync-boundaries
"""

import logging
from datetime import datetime

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.models.farm import Farm
from app.services.agtrail_boundary_client import (
    BATCH_SIZE_LIMIT,
    geojson_polygon_to_wkt,
    get_farm_boundaries_batch,
)

logger = logging.getLogger(__name__)


def _chunk(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _apply_boundary_item(farm: Farm, item: dict) -> bool:
    """Updates farm from one item in the batch response. Returns whether anything changed."""
    updated_at_raw = item.get("updatedAt")
    updated_at = datetime.fromisoformat(updated_at_raw.replace("Z", "+00:00")) if updated_at_raw else None

    # AgTrail's own docs: if updatedAt hasn't changed since our last
    # sync, there's nothing new to write. Saves a write on every farm,
    # every month, once its boundary has stabilized.
    if updated_at is not None and farm.boundary_updated_at == updated_at:
        return False

    farm.boundary_mapped = bool(item.get("mapped"))
    farm.boundary_updated_at = updated_at
    geometry = item.get("geometry")
    farm.boundary = geojson_polygon_to_wkt(geometry) if (farm.boundary_mapped and geometry) else None
    return True


def sync_boundaries() -> dict:
    """Returns a summary: {considered, updated, unchanged, missing}."""
    summary = {"considered": 0, "updated": 0, "unchanged": 0, "missing": 0}

    with SessionLocal() as db:
        farms = db.execute(
            select(Farm).where(Farm.agtrail_farm_id.is_not(None))
        ).scalars().all()
        by_agtrail_id = {farm.agtrail_farm_id: farm for farm in farms}
        summary["considered"] = len(farms)

        for batch_ids in _chunk(list(by_agtrail_id), BATCH_SIZE_LIMIT):
            items, missing_ids = get_farm_boundaries_batch(batch_ids)

            for item in items:
                farm = by_agtrail_id.get(item.get("farmId"))
                if farm is None:
                    continue
                try:
                    changed = _apply_boundary_item(farm, item)
                except ValueError as exc:
                    logger.warning("Skipping unparsable boundary for farm %s: %s", farm.agtrail_farm_id, exc)
                    continue
                summary["updated" if changed else "unchanged"] += 1

            if missing_ids:
                summary["missing"] += len(missing_ids)
                logger.warning("AgTrail doesn't recognize %d farm id(s): %s", len(missing_ids), missing_ids)

            db.commit()

    return summary


if __name__ == "__main__":
    configure_logging()
    result = sync_boundaries()
    logger.info(
        "Boundary sync done: %d farm(s) considered, %d updated, %d unchanged, %d not recognized by AgTrail.",
        result["considered"], result["updated"], result["unchanged"], result["missing"],
    )
