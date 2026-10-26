"""
Rebuilds crop cycles from the farm.crop_cycle.created events the hub already accepted and
stored in agtrail_sync_log, for events that arrived before crop cycles were saved as records
(most of them came on 21 Sep 2026; the cycle tables were added on 22 Oct). Safe to run again:
a cycle is matched on AgTrail's own cycle id, and the farm must already exist.

    python -m app.jobs.backfill_crop_cycles

Usage from Makefile: make backfill-crop-cycles
"""

import logging
from collections import Counter

from pydantic import ValidationError
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.core.signal_categories import normalise_label
from app.models.farm import Farm
from app.models.sync_log import AgtrailSyncLog, SyncDirection
from app.schemas.agtrail_webhook import WebhookCropCycle
from app.services.farm_records import upsert_crop_cycle

logger = logging.getLogger(__name__)

COMMIT_EVERY = 100
EVENT_TYPE = "farm.crop_cycle.created"


def backfill_crop_cycles() -> dict:
    summary = {"events_considered": 0, "cycles_created": 0, "cycles_updated": 0, "skipped_no_farm": 0, "skipped_bad_data": 0,
               "crops_without_kb_file": Counter()}

    with SessionLocal() as db:
        log_ids = db.scalars(
            select(AgtrailSyncLog.id)
            .where(AgtrailSyncLog.direction == SyncDirection.inbound)
            .where(AgtrailSyncLog.payload["event_type"].astext == EVENT_TYPE)
            .order_by(AgtrailSyncLog.synced_at)
        ).all()

        for done, log_id in enumerate(log_ids, start=1):
            payload = db.get(AgtrailSyncLog, log_id).payload or {}
            summary["events_considered"] += 1
            try:
                cycle_data = WebhookCropCycle(**(payload.get("crop_cycle") or {}))
            except ValidationError:
                summary["skipped_bad_data"] += 1
                continue

            farm_ref = (payload.get("farm") or {}).get("agtrail_farm_id")
            farm = db.scalar(select(Farm).where(Farm.agtrail_farm_id == farm_ref)) if farm_ref else None
            if farm is None:
                summary["skipped_no_farm"] += 1
                continue

            cycle, created = upsert_crop_cycle(db, farm, cycle_data)
            summary["cycles_created" if created else "cycles_updated"] += 1
            if cycle.crop_id is None:
                summary["crops_without_kb_file"][normalise_label(cycle.crop_raw)] += 1

            if done % COMMIT_EVERY == 0:
                db.commit()
        db.commit()

    return summary


if __name__ == "__main__":
    configure_logging()
    result = backfill_crop_cycles()
    logger.info(
        "Crop cycle backfill done: %d event(s) considered, %d cycle(s) created, %d already there, %d skipped (no farm), %d skipped (bad data).",
        result["events_considered"], result["cycles_created"], result["cycles_updated"],
        result["skipped_no_farm"], result["skipped_bad_data"],
    )
    if result["crops_without_kb_file"]:
        logger.warning("Crop texts with no Knowledge Base file: %s", dict(result["crops_without_kb_file"]))
