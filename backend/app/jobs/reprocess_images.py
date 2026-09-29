"""
Repair tool for the job queue, not the primary retry path (that's
app/worker.py and its automatic backoff/requeue). Run this from cron as a
periodic safety net, and by hand after an incident:

  1. Requeues jobs stuck "running" because the worker that claimed them
     died mid-job.
  2. Enqueues a run_inference job for any pending image that has no job
     at all, e.g. one created before this queue existed, or from a rare
     failure between saving the image and enqueueing its job.

Usage, from the backend directory:
    python -m app.jobs.reprocess_images
"""

import logging

from sqlalchemy import String, cast, select

from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.models.image import FarmImage, ImageStatus
from app.models.job import Job, JobStatus
from app.services.job_queue import JOB_RUN_INFERENCE, enqueue, requeue_stale

logger = logging.getLogger(__name__)


def _images_missing_a_job(db) -> list:
    has_job = (
        select(Job.payload["image_id"].astext)
        .where(
            Job.job_type == JOB_RUN_INFERENCE,
            Job.status != JobStatus.dead,
            # A NULL in this list would make NOT IN below match nothing at
            # all (SQL's three-valued logic), so keep NULLs out of it.
            Job.payload["image_id"].astext.is_not(None),
        )
    )
    return db.execute(
        select(FarmImage.id)
        .where(FarmImage.status == ImageStatus.pending)
        .where(cast(FarmImage.id, String).not_in(has_job))
    ).scalars().all()


def repair(stale_after_seconds: int = 600) -> tuple[int, int]:
    """Returns (jobs_requeued, jobs_enqueued)."""
    with SessionLocal() as db:
        requeued = requeue_stale(db, stale_after_seconds=stale_after_seconds)

        orphaned_image_ids = _images_missing_a_job(db)
        for image_id in orphaned_image_ids:
            enqueue(db, JOB_RUN_INFERENCE, {"image_id": str(image_id)})
        if orphaned_image_ids:
            db.commit()

    return requeued, len(orphaned_image_ids)


if __name__ == "__main__":
    configure_logging()
    requeued, enqueued = repair()
    logger.info("Requeued %d stale job(s), enqueued %d orphaned image(s).", requeued, enqueued)
