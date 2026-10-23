"""
Queues an archive job for every image that has not been copied to our storage
yet, for photos that arrived before archiving was switched on. Safe to run again:
an image that already has a live archive job is skipped, and archiving an image
that is already archived does nothing.

    python -m app.jobs.backfill_archive

Does nothing, and says so, when no bucket is configured. The worker
(app.worker) then does the copying a few at a time.
"""

import logging

from sqlalchemy import String, cast, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.models.image import FarmImage
from app.models.job import Job, JobStatus
from app.services.job_queue import JOB_ARCHIVE_IMAGE, enqueue

logger = logging.getLogger(__name__)


def _images_to_archive(db) -> list:
    has_job = select(Job.payload["image_id"].astext).where(
        Job.job_type == JOB_ARCHIVE_IMAGE,
        Job.status != JobStatus.dead,
        Job.payload["image_id"].astext.is_not(None),
    )
    return db.execute(
        select(FarmImage.id)
        .where(FarmImage.archived_url.is_(None))
        .where(cast(FarmImage.id, String).not_in(has_job))
    ).scalars().all()


def backfill() -> int:
    """Returns how many archive jobs were queued."""
    if not settings.archive_enabled:
        logger.warning("No storage bucket is configured (S3_BUCKET or GCP_BUCKET_NAME), nothing queued.")
        return 0
    with SessionLocal() as db:
        image_ids = _images_to_archive(db)
        for image_id in image_ids:
            enqueue(db, JOB_ARCHIVE_IMAGE, {"image_id": str(image_id)})
        if image_ids:
            db.commit()
    return len(image_ids)


def main() -> None:
    configure_logging()
    queued = backfill()
    logger.info("Archive backfill queued %d image(s).", queued)


if __name__ == "__main__":
    main()
