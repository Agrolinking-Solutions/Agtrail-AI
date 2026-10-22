"""
Fills crop_cycles and farm_operations from the raw webhook copies already
stored on farm_images.source_payload_raw, for events that arrived before
those tables existed. Safe to run again: cycles and operations are matched
on AgTrail's own ids, and photos already linked are skipped.

    python -m app.jobs.backfill_operations

Usage from Makefile: make backfill-operations

Prints which operation names and crop texts it could not map, so the team
can add them to operation_type_aliases and crop_aliases.
"""

import logging
from collections import Counter
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.core.signal_categories import normalise_label
from app.models.image import FarmImage
from app.schemas.agtrail_webhook import WebhookCropCycle, WebhookOperationContext
from app.services.farm_records import record_operation, upsert_crop_cycle

logger = logging.getLogger(__name__)

COMMIT_EVERY = 100


def _captured_at(image: FarmImage, payload: dict) -> datetime | None:
    if image.captured_at is not None:
        return image.captured_at
    raw = (payload.get("image") or {}).get("captured_at")
    return datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None


def backfill_operations() -> dict:
    summary = {
        "images_considered": 0,
        "images_linked": 0,
        "cycles_created": 0,
        "operations_created": 0,
        "skipped": 0,
        "unmapped_operation_types": Counter(),
        "crops_without_kb_file": Counter(),
    }

    with SessionLocal() as db:
        image_ids = db.scalars(
            select(FarmImage.id)
            .where(FarmImage.source_payload_raw.is_not(None))
            .where(FarmImage.farm_operation_id.is_(None))
            .order_by(FarmImage.captured_at)
        ).all()

        for done, image_id in enumerate(image_ids, start=1):
            image = db.get(FarmImage, image_id)
            payload = image.source_payload_raw or {}
            summary["images_considered"] += 1

            try:
                cycle_data = WebhookCropCycle(**(payload.get("crop_cycle") or {}))
                context = WebhookOperationContext(**(payload.get("operation_context") or {}))
            except ValidationError:
                summary["skipped"] += 1
                continue

            captured_at = _captured_at(image, payload)
            farm = image.visit.farm
            if captured_at is None or farm is None:
                summary["skipped"] += 1
                continue

            cycle, cycle_created = upsert_crop_cycle(db, farm, cycle_data)
            summary["cycles_created"] += int(cycle_created)
            if cycle.crop_id is None:
                summary["crops_without_kb_file"][normalise_label(cycle.crop_raw)] += 1

            operation, op_created = record_operation(db, farm, cycle, context, captured_at)
            summary["operations_created"] += int(op_created)
            if operation is not None:
                image.farm_operation_id = operation.id
                summary["images_linked"] += 1
                if operation.signal_category is None:
                    summary["unmapped_operation_types"][normalise_label(operation.logged_type_raw)] += 1

            if done % COMMIT_EVERY == 0:
                db.commit()
        db.commit()

    return summary


if __name__ == "__main__":
    configure_logging()
    result = backfill_operations()
    logger.info(
        "Backfill done: %d image(s) considered, %d linked to an operation, %d cycle(s) and %d operation(s) created, %d skipped.",
        result["images_considered"], result["images_linked"], result["cycles_created"],
        result["operations_created"], result["skipped"],
    )
    if result["unmapped_operation_types"]:
        logger.warning("Operation names with no alias: %s", dict(result["unmapped_operation_types"]))
    if result["crops_without_kb_file"]:
        logger.warning(
            "Crop texts with no Knowledge Base file (counted per photo): %s",
            dict(result["crops_without_kb_file"]),
        )
