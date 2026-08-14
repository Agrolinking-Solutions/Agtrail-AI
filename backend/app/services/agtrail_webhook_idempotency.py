from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.sync_log import AgtrailSyncLog, SyncDirection, SyncStatus


def event_already_received(db: Session, event_id: str) -> bool:
    """
    Checks whether we've already logged this exact event_id from AgTrail.
    Used to safely ignore retried webhook deliveries instead of
    processing the same event twice.
    """
    existing = db.execute(
        select(AgtrailSyncLog).where(
            AgtrailSyncLog.received_event_id == event_id,
            AgtrailSyncLog.direction == SyncDirection.inbound,
        )
    ).scalar_one_or_none()

    return existing is not None


def record_event_received(db: Session, event_id: str, farm_id, raw_payload: dict) -> AgtrailSyncLog:
    """
    Logs that we've received and are about to process this event.
    Written to the database and committed before any mapping or
    prediction work happens, so a duplicate delivery is caught even
    if this request crashes partway through processing.
    """
    log_entry = AgtrailSyncLog(
        farm_id=farm_id,
        direction=SyncDirection.inbound,
        received_event_id=event_id,
        sync_status=SyncStatus.pending,
        payload=raw_payload,
    )
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)
    return log_entry