"""
Processes jobs from the queue. Two ways to run this:

    python -m app.worker            # long-running: polls forever
    python -m app.worker --once     # cron mode: drains the queue, then exits

Use --once from a scheduled job (e.g. a Render Cron Job every few
minutes) instead of paying for an always-on worker process. Predictions
then lag by up to the schedule's interval rather than landing within
seconds, which is an acceptable trade for the cost saving at low volume.
Either mode coordinates safely with the other through Postgres row
locks, so switching later doesn't require touching anything else.
"""

import argparse
import logging
import os
import signal
import socket
import time
import uuid

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.models.job import Job
from app.services.image_archive import archive_image_for_id
from app.services.inference import run_inference_for_image
from app.services.job_queue import (
    JOB_ARCHIVE_IMAGE,
    JOB_RUN_INFERENCE,
    claim_next,
    mark_failed,
    mark_succeeded,
    requeue_stale,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = float(os.environ.get("WORKER_POLL_INTERVAL_SECONDS", "5"))
STALE_CHECK_INTERVAL_SECONDS = 60

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _handle_run_inference(payload: dict) -> None:
    image_id = uuid.UUID(payload["image_id"])
    run_inference_for_image(image_id)


def _handle_archive_image(payload: dict) -> None:
    image_id = uuid.UUID(payload["image_id"])
    archive_image_for_id(image_id)


HANDLERS = {
    JOB_RUN_INFERENCE: _handle_run_inference,
    JOB_ARCHIVE_IMAGE: _handle_archive_image,
}


def run_one(worker_id: str = WORKER_ID) -> bool:
    """Claims and runs a single due job. Returns whether one was found."""
    with SessionLocal() as db:
        job = claim_next(db, job_types=list(HANDLERS), worker_id=worker_id)
        if job is None:
            return False

        handler = HANDLERS[job.job_type]
        try:
            handler(job.payload)
        except Exception as exc:
            logger.exception("Job %s (%s) raised", job.id, job.job_type)
            mark_failed(db, job, repr(exc))
        else:
            mark_succeeded(db, job)
        return True


def run_until_empty(worker_id: str = WORKER_ID) -> int:
    """
    Processes due jobs until none are left, then returns. For a scheduled
    (cron) invocation rather than a long-running process: also requeues
    any stale jobs first, since there's no ongoing loop to do that
    periodically between runs.
    """
    with SessionLocal() as db:
        requeue_stale(db)

    processed = 0
    while run_one(worker_id):
        processed += 1
    return processed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--once", action="store_true",
        help="Drain the queue once and exit, instead of polling forever. For cron-style scheduling.",
    )
    args = parser.parse_args()

    configure_logging()

    if args.once:
        logger.info("Worker %s starting in --once mode.", WORKER_ID)
        processed = run_until_empty()
        logger.info("Worker %s processed %d job(s), queue empty, exiting.", WORKER_ID, processed)
        return

    logger.info("Worker %s starting, handling: %s", WORKER_ID, list(HANDLERS))

    shutdown = {"requested": False}

    def _request_shutdown(signum, frame):
        logger.info("Worker %s received signal %d, shutting down after the current job.", WORKER_ID, signum)
        shutdown["requested"] = True

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    last_stale_check = 0.0

    while not shutdown["requested"]:
        if time.monotonic() - last_stale_check > STALE_CHECK_INTERVAL_SECONDS:
            with SessionLocal() as db:
                requeue_stale(db)
            last_stale_check = time.monotonic()

        if not run_one():
            time.sleep(POLL_INTERVAL_SECONDS)

    logger.info("Worker %s stopped.", WORKER_ID)


if __name__ == "__main__":
    main()
