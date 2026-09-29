"""Tests for the Postgres-backed job queue and worker, against a real database."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from tests.conftest import requires_db

pytestmark = requires_db


def test_enqueue_then_claim(db_session):
    from app.models.job import Job, JobStatus
    from app.services.job_queue import enqueue, claim_next

    job = enqueue(db_session, "run_inference", {"image_id": "abc"})
    db_session.commit()

    claimed = claim_next(db_session, job_types=["run_inference"], worker_id="w1")

    assert claimed.id == job.id
    assert claimed.status == JobStatus.running
    assert claimed.attempts == 1
    assert claimed.locked_by == "w1"


def test_claim_next_ignores_other_job_types(db_session):
    from app.services.job_queue import enqueue, claim_next

    enqueue(db_session, "some_other_job", {})
    db_session.commit()

    assert claim_next(db_session, job_types=["run_inference"], worker_id="w1") is None


def test_claim_next_returns_none_when_empty(db_session):
    from app.services.job_queue import claim_next

    assert claim_next(db_session, job_types=["run_inference"], worker_id="w1") is None


def test_claim_next_skips_jobs_not_yet_due(db_session):
    from app.models.job import Job
    from app.services.job_queue import enqueue, claim_next

    job = enqueue(db_session, "run_inference", {"image_id": "abc"})
    job.run_at = datetime.now(timezone.utc) + timedelta(hours=1)
    db_session.commit()

    assert claim_next(db_session, job_types=["run_inference"], worker_id="w1") is None


def test_two_workers_never_claim_the_same_job(migrated_db):
    """
    The core guarantee of SELECT ... FOR UPDATE SKIP LOCKED: two workers
    polling at the same time each get a distinct job, never both getting
    the one row. Uses two real sessions/connections, since a single
    session can't model two concurrent workers.
    """
    from app.core.database import SessionLocal
    from app.services.job_queue import enqueue, claim_next

    with SessionLocal() as setup:
        job_a = enqueue(setup, "run_inference", {"image_id": "a"})
        job_b = enqueue(setup, "run_inference", {"image_id": "b"})
        setup.commit()
        job_a_id, job_b_id = job_a.id, job_b.id

    with SessionLocal() as db_1, SessionLocal() as db_2:
        claimed_1 = claim_next(db_1, job_types=["run_inference"], worker_id="w1")
        claimed_2 = claim_next(db_2, job_types=["run_inference"], worker_id="w2")

        assert {claimed_1.id, claimed_2.id} == {job_a_id, job_b_id}


def test_mark_succeeded(db_session):
    from app.models.job import JobStatus
    from app.services.job_queue import enqueue, claim_next, mark_succeeded

    enqueue(db_session, "run_inference", {"image_id": "abc"})
    db_session.commit()
    job = claim_next(db_session, job_types=["run_inference"], worker_id="w1")

    mark_succeeded(db_session, job)

    assert job.status == JobStatus.succeeded
    assert job.locked_by is None


def test_mark_failed_retries_with_backoff(db_session):
    from app.models.job import JobStatus
    from app.services.job_queue import enqueue, claim_next, mark_failed

    enqueue(db_session, "run_inference", {"image_id": "abc"}, max_attempts=3)
    db_session.commit()
    job = claim_next(db_session, job_types=["run_inference"], worker_id="w1")

    mark_failed(db_session, job, "boom", backoff_base_seconds=30)

    assert job.status == JobStatus.queued
    assert job.attempts == 1
    assert job.last_error == "boom"
    assert job.run_at > datetime.now(timezone.utc) + timedelta(seconds=20)


def test_mark_failed_gives_up_after_max_attempts(db_session):
    from app.models.job import JobStatus
    from app.services.job_queue import enqueue, claim_next, mark_failed

    enqueue(db_session, "run_inference", {"image_id": "abc"}, max_attempts=1)
    db_session.commit()
    job = claim_next(db_session, job_types=["run_inference"], worker_id="w1")

    mark_failed(db_session, job, "still broken")

    assert job.status == JobStatus.dead


def test_requeue_stale_recovers_a_crashed_worker(db_session):
    from app.models.job import Job, JobStatus
    from app.services.job_queue import enqueue, claim_next, requeue_stale

    enqueue(db_session, "run_inference", {"image_id": "abc"})
    db_session.commit()
    job = claim_next(db_session, job_types=["run_inference"], worker_id="dead-worker")
    job.locked_at = datetime.now(timezone.utc) - timedelta(seconds=700)
    db_session.commit()

    requeued_count = requeue_stale(db_session, stale_after_seconds=600)

    assert requeued_count == 1
    db_session.refresh(job)
    assert job.status == JobStatus.queued
    assert job.locked_by is None


def test_requeue_stale_leaves_recent_jobs_alone(db_session):
    from app.models.job import JobStatus
    from app.services.job_queue import enqueue, claim_next, requeue_stale

    enqueue(db_session, "run_inference", {"image_id": "abc"})
    db_session.commit()
    job = claim_next(db_session, job_types=["run_inference"], worker_id="w1")

    assert requeue_stale(db_session, stale_after_seconds=600) == 0
    assert job.status == JobStatus.running


def test_worker_run_one_dispatches_and_marks_succeeded(db_session, monkeypatch):
    from app.models.job import Job, JobStatus
    from app.services.job_queue import enqueue

    import app.worker as worker

    seen = []
    monkeypatch.setitem(worker.HANDLERS, "run_inference", lambda payload: seen.append(payload))

    enqueue(db_session, "run_inference", {"image_id": "abc"})
    db_session.commit()

    processed = worker.run_one(worker_id="test-worker")

    assert processed is True
    assert seen == [{"image_id": "abc"}]
    job = db_session.execute(select(Job)).scalar_one()
    assert job.status == JobStatus.succeeded


def test_worker_run_one_marks_failed_on_exception(db_session, monkeypatch):
    from app.models.job import Job, JobStatus
    from app.services.job_queue import enqueue

    import app.worker as worker

    def boom(payload):
        raise RuntimeError("model not found")

    monkeypatch.setitem(worker.HANDLERS, "run_inference", boom)

    enqueue(db_session, "run_inference", {"image_id": "abc"}, max_attempts=1)
    db_session.commit()

    assert worker.run_one(worker_id="test-worker") is True

    job = db_session.execute(select(Job)).scalar_one()
    assert job.status == JobStatus.dead
    assert "model not found" in job.last_error


def test_worker_run_one_returns_false_when_queue_empty(db_session):
    import app.worker as worker

    assert worker.run_one(worker_id="test-worker") is False


def test_run_until_empty_drains_the_queue_and_stops(db_session, monkeypatch):
    from app.models.job import Job, JobStatus
    from app.services.job_queue import enqueue

    import app.worker as worker

    seen = []
    monkeypatch.setitem(worker.HANDLERS, "run_inference", lambda payload: seen.append(payload["image_id"]))

    for image_id in ("a", "b", "c"):
        enqueue(db_session, "run_inference", {"image_id": image_id})
    db_session.commit()

    processed = worker.run_until_empty(worker_id="cron-worker")

    assert processed == 3
    assert sorted(seen) == ["a", "b", "c"]
    jobs = db_session.execute(select(Job)).scalars().all()
    assert all(job.status == JobStatus.succeeded for job in jobs)
    # Nothing left to claim, a second call is a no-op rather than hanging.
    assert worker.run_until_empty(worker_id="cron-worker") == 0


def test_run_until_empty_requeues_stale_jobs_first(db_session):
    from datetime import datetime, timedelta, timezone

    from app.models.job import JobStatus
    from app.services.job_queue import enqueue, claim_next

    import app.worker as worker

    enqueue(db_session, "run_inference", {"image_id": "abc"})
    db_session.commit()
    stuck = claim_next(db_session, job_types=["run_inference"], worker_id="dead-worker")
    stuck.locked_at = datetime.now(timezone.utc) - timedelta(seconds=700)
    db_session.commit()

    # No handler swap here: the real _handle_run_inference will raise
    # (image "abc" doesn't exist), which is fine, it only needs to prove
    # the stale job was picked up at all, not that it succeeds.
    worker.run_until_empty(worker_id="cron-worker")

    db_session.refresh(stuck)
    assert stuck.status != JobStatus.running
