"""
Polls the jobs table and runs whatever is due. Run one or more of these
as long-lived processes (see infra/docker/docker-compose*.yml); they
coordinate safely through Postgres row locks, so scaling up is just
starting more of them.

Usage:
    python -m app.worker
"""

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
from app.services.inference import run_inference_for_image
from app.services.job_queue import (
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


HANDLERS = {
    JOB_RUN_INFERENCE: _handle_run_inference,
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


def main() -> None:
    configure_logging()
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
