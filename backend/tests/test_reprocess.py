"""Tests for the app.jobs.reprocess_images repair tool, against a real database."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from tests.conftest import load_payload, post_event, queued_image_ids, requires_db

pytestmark = requires_db


def test_repair_requeues_stale_jobs(client, db_session):
    from app.jobs.reprocess_images import repair
    from app.models.job import Job, JobStatus
    from app.services.job_queue import claim_next

    post_event(client, load_payload("test_payload.json"))
    job = claim_next(db_session, job_types=["run_inference"], worker_id="dead-worker")
    job.locked_at = datetime.now(timezone.utc) - timedelta(seconds=700)
    db_session.commit()

    requeued, enqueued = repair()

    assert requeued == 1
    assert enqueued == 0
    db_session.refresh(job)
    assert job.status == JobStatus.queued


def test_repair_enqueues_orphaned_pending_images(client, db_session):
    from app.jobs.reprocess_images import repair
    from app.models.image import FarmImage
    from app.models.job import Job

    post_event(client, load_payload("test_payload.json"))
    # Simulate an image that predates the queue: its job is gone, but the
    # image itself is still pending.
    db_session.execute(Job.__table__.delete())
    db_session.commit()

    requeued, enqueued = repair()

    assert requeued == 0
    assert enqueued == 1
    image = db_session.execute(select(FarmImage)).scalar_one()
    assert queued_image_ids(db_session) == [str(image.id)]


def test_repair_leaves_images_with_a_live_job_alone(client, db_session):
    from app.jobs.reprocess_images import repair

    post_event(client, load_payload("test_payload.json"))

    requeued, enqueued = repair()

    assert requeued == 0
    assert enqueued == 0
    assert len(queued_image_ids(db_session)) == 1
