"""
Test setup. Settings are read at import time, so the environment is set
here before anything from app is imported.

Database tests need a disposable PostGIS database in TEST_DATABASE_URL,
e.g. from the repository root:
    docker run -d --name agtrail_test_db -p 127.0.0.1:55432:5432 \\
        -e POSTGRES_PASSWORD=test postgis/postgis:16-3.4
    TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres?sslmode=disable \\
        pytest
Without it, only the database-free tests run. Never point it at a real
database, every table is emptied between tests.
"""

import hashlib
import hmac
import json
import os
from pathlib import Path

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
WEBHOOK_SECRET = "test-webhook-secret"
API_KEY = "test-api-key-value"

os.environ.update({
    "ENVIRONMENT": "test",
    "SECRET_KEY": "test-secret-key",
    "DATABASE_URL": TEST_DATABASE_URL or "postgresql://unused:unused@127.0.0.1:1/unused?sslmode=disable",
    "AGTRAIL_WEBHOOK_SECRET": WEBHOOK_SECRET,
    "AGTRAIL_OUTBOUND_SECRET": "",
    "API_KEYS": f"tester:{API_KEY}",
    "IMAGE_ALLOW_HTTP": "false",
    # Storage is off unless a test turns it on, whatever a developer has in their own .env.
    "S3_ENDPOINT": "",
    "S3_BUCKET": "",
    "S3_ACCESS_KEY": "",
    "S3_SECRET_KEY": "",
    "GCP_BUCKET_NAME": "",
})

BACKEND_DIR = Path(__file__).resolve().parents[1]

requires_db = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")


def sign(body: bytes) -> str:
    return hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def load_payload(name: str) -> dict:
    return json.loads((BACKEND_DIR / name).read_text())


@pytest.fixture(scope="session")
def migrated_db():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from app.core.database import engine

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(config, "head")
    return engine


@pytest.fixture
def db_session(migrated_db):
    from sqlalchemy import text

    from app.core.database import SessionLocal

    with migrated_db.begin() as conn:
        conn.execute(text(
            "TRUNCATE agtrail_sync_log, predictions, farm_images, farm_visits, "
            "monitoring_cycles, farms, farmers, agents, jobs, trashed_records CASCADE"
        ))
    with SessionLocal() as session:
        yield session


class _StandInKnowledgeBase:
    """Serves the captured taxonomy, so no test reaches the real Knowledge Base over the network."""

    def taxonomy(self):
        from app.clients.base import ClientResult
        from app.contracts.knowledge_base import Taxonomy

        path = Path(__file__).parent / "fixtures" / "knowledge_base" / "taxonomy.json"
        return ClientResult(value=Taxonomy.model_validate(json.loads(path.read_text())))


@pytest.fixture
def client(db_session):
    from fastapi.testclient import TestClient

    from app.api.routes.farms import knowledge_base
    from app.main import app

    app.dependency_overrides[knowledge_base] = _StandInKnowledgeBase
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(knowledge_base, None)


def post_event(client, payload: dict, signature: str | None = "auto"):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if signature == "auto":
        headers["X-AgTrail-Signature"] = sign(body)
    elif signature is not None:
        headers["X-AgTrail-Signature"] = signature
    return client.post("/webhooks/agtrail/farm-event", content=body, headers=headers)


def queued_image_ids(db_session, job_type="run_inference") -> list[str]:
    """image_id values from queued/running jobs of the given type, for
    asserting the webhook enqueued work without actually running inference."""
    from sqlalchemy import select

    from app.models.job import Job

    jobs = db_session.execute(select(Job).where(Job.job_type == job_type)).scalars().all()
    return [job.payload["image_id"] for job in jobs]
