"""
A background job queue backed by the same Postgres database as everything
else, no separate broker (Redis, RabbitMQ) required. Workers claim rows
with SELECT ... FOR UPDATE SKIP LOCKED, so any number of worker processes
can poll the same table without double-processing a job.

Enqueue inside the same transaction as the work that created the job (see
the webhook handler), so a job is never lost: either both the database
rows and the job commit together, or neither do. This is the main
advantage over FastAPI's BackgroundTasks, which runs in-process and is
silently lost if the process crashes or restarts before it runs.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.job import Job, JobStatus

logger = logging.getLogger(__name__)

JOB_RUN_INFERENCE = "run_inference"

DEFAULT_BACKOFF_SECONDS = 30
MAX_BACKOFF_SECONDS = 3600
STALE_AFTER_SECONDS = 600


def enqueue(db: Session, job_type: str, payload: dict, *, max_attempts: int = 5) -> Job:
    """
    Adds a job to the session. Does not commit: call this alongside the
    rest of the work in a request and let the caller commit once, so the
    job and that work land together.
    """
    job = Job(job_type=job_type, payload=payload, max_attempts=max_attempts)
    db.add(job)
    db.flush()
    return job


def claim_next(db: Session, job_types: list[str], worker_id: str) -> Job | None:
    """
    Locks and returns the oldest due job of the given types, marking it
    running and incrementing its attempt count. Commits immediately, so
    the row lock is held only for the moment it takes to mark it claimed;
    other workers' SKIP LOCKED queries then simply pass over it. Returns
    None if nothing is due.
    """
    job = db.execute(
        select(Job)
        .where(
            Job.status == JobStatus.queued,
            Job.job_type.in_(job_types),
            Job.run_at <= datetime.now(timezone.utc),
        )
        .order_by(Job.run_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()

    if job is None:
        return None

    job.status = JobStatus.running
    job.attempts += 1
    job.locked_by = worker_id
    job.locked_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return job


def mark_succeeded(db: Session, job: Job) -> None:
    job.status = JobStatus.succeeded
    job.locked_by = None
    db.commit()


def mark_failed(
    db: Session,
    job: Job,
    error: str,
    *,
    backoff_base_seconds: int = DEFAULT_BACKOFF_SECONDS,
    backoff_max_seconds: int = MAX_BACKOFF_SECONDS,
) -> None:
    """
    Records the error. Reschedules with exponential backoff if attempts
    remain, otherwise marks the job dead rather than retrying forever.
    """
    job.last_error = error[:2000]
    job.locked_by = None

    if job.attempts >= job.max_attempts:
        job.status = JobStatus.dead
        logger.error(
            "Job %s (%s) exhausted %d attempts, giving up: %s",
            job.id, job.job_type, job.attempts, error,
        )
    else:
        delay = min(backoff_base_seconds * (2 ** (job.attempts - 1)), backoff_max_seconds)
        job.status = JobStatus.queued
        job.run_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        logger.warning(
            "Job %s (%s) failed on attempt %d/%d, retrying in %ds: %s",
            job.id, job.job_type, job.attempts, job.max_attempts, delay, error,
        )

    db.commit()


def requeue_stale(db: Session, stale_after_seconds: int = STALE_AFTER_SECONDS) -> int:
    """
    Puts jobs stuck in running back into queued. A job stays running
    longer than this only if the worker that claimed it crashed or was
    killed before finishing. Safe to call regularly, e.g. once per worker
    poll loop and from a scheduled repair task.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
    stale_jobs = db.execute(
        select(Job).where(Job.status == JobStatus.running, Job.locked_at < cutoff)
    ).scalars().all()

    for job in stale_jobs:
        job.status = JobStatus.queued
        job.locked_by = None
        logger.warning("Requeuing stale job %s (%s), last locked at %s.", job.id, job.job_type, job.locked_at)

    if stale_jobs:
        db.commit()
    return len(stale_jobs)
