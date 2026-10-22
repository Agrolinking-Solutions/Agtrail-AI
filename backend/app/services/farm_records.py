"""
Saves what AgTrail tells us about crop cycles and logged operations as real
rows. Used by the webhook as events arrive, and by the backfill job for
events that arrived before these tables existed. Both paths go through the
same functions, so they cannot drift apart.
"""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.signal_categories import LOCAL_TIMEZONE, normalise_label
from app.models.aliases import CropAlias, OperationTypeAlias
from app.models.crop_cycle import CropCycle
from app.models.farm import Farm
from app.models.farm_operation import FarmOperation

logger = logging.getLogger(__name__)


def resolve_crop(db: Session, raw: str) -> tuple[str | None, str | None]:
    """(clean crop name, Knowledge Base crop id) for the text a user typed.
    Either can be None: the first when we have never seen the text, the
    second when the crop has no Knowledge Base file yet."""
    alias = db.get(CropAlias, normalise_label(raw))
    if alias is None:
        return None, None
    return alias.canonical_name, alias.kb_crop_id


def resolve_signal_category(db: Session, raw: str) -> str | None:
    alias = db.get(OperationTypeAlias, normalise_label(raw))
    return alias.signal_category if alias else None


def upsert_crop_cycle(db: Session, farm: Farm, data) -> tuple[CropCycle, bool]:
    """Creates the cycle, or updates it if AgTrail has told us about it
    before. `data` is a WebhookCropCycle. Returns (cycle, was_created)."""
    canonical, kb_crop_id = resolve_crop(db, data.crop)

    cycle = db.execute(
        select(CropCycle).where(CropCycle.agtrail_cycle_id == data.agtrail_cycle_id)
    ).scalar_one_or_none()

    if cycle is None:
        cycle = CropCycle(
            farm_id=farm.id,
            agtrail_cycle_id=data.agtrail_cycle_id,
            crop_raw=data.crop,
            crop_canonical=canonical,
            crop_id=kb_crop_id,
            variety=data.variety,
            planted_date=data.planted_date,
            status=data.status,
        )
        db.add(cycle)
        db.flush()
        return cycle, True

    if cycle.farm_id != farm.id:
        # An id is never reused across farms. Don't silently move it.
        logger.warning(
            "Crop cycle %s is saved on another farm than the one in this event, leaving it there.",
            data.agtrail_cycle_id,
        )
        return cycle, False

    cycle.crop_raw = data.crop
    cycle.crop_canonical = canonical
    cycle.crop_id = kb_crop_id
    cycle.variety = data.variety
    cycle.planted_date = data.planted_date
    cycle.status = data.status
    db.flush()
    return cycle, False


def record_operation(
    db: Session,
    farm: Farm,
    cycle: CropCycle,
    context,
    captured_at: datetime,
) -> tuple[FarmOperation | None, bool]:
    """Saves the operation an agent logged with a photo. `context` is a
    WebhookOperationContext or None. Returns (operation, was_created), or
    (None, False) when no operation type came with the photo."""
    if context is None or not context.logged_operation_type:
        return None, False

    raw_type = context.logged_operation_type
    operation_date = captured_at.astimezone(LOCAL_TIMEZONE).date()

    if context.agtrail_operation_id:
        operation = db.execute(
            select(FarmOperation).where(FarmOperation.agtrail_operation_id == context.agtrail_operation_id)
        ).scalar_one_or_none()
        if operation is not None:
            # Another photo for the same operation. The operation's date is
            # its earliest photo.
            if operation_date < operation.operation_date:
                operation.operation_date = operation_date
            operation.logged_type_raw = raw_type
            operation.signal_category = resolve_signal_category(db, raw_type)
            db.flush()
            return operation, False

    operation = FarmOperation(
        farm_id=farm.id,
        crop_cycle_id=cycle.id,
        agtrail_operation_id=context.agtrail_operation_id,
        logged_type_raw=raw_type,
        signal_category=resolve_signal_category(db, raw_type),
        operation_date=operation_date,
    )
    db.add(operation)
    db.flush()
    return operation, True
