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
        select(AgtrailSyncLog.id).where(
            AgtrailSyncLog.received_event_id == event_id,
            AgtrailSyncLog.direction == SyncDirection.inbound,
        )
    ).scalar_one_or_none()

    return existing is not None


def record_event_received(db: Session, event_id: str, farm_id, raw_payload: dict) -> AgtrailSyncLog:
    """
    Logs that this event has been received and processed. Added to the
    caller's transaction rather than committed here, so the log entry and
    the records created from the event are saved together or not at all.
    The unique constraint on received_event_id makes a concurrent
    duplicate delivery fail at commit instead of being processed twice.
    """
    log_entry = AgtrailSyncLog(
        farm_id=farm_id,
        direction=SyncDirection.inbound,
        received_event_id=event_id,
        sync_status=SyncStatus.success,
        payload=raw_payload,
    )
    db.add(log_entry)
    return log_entry
