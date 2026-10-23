"""Tests for copying a farm image into our own storage, GCS calls mocked."""

import hashlib

from tests.conftest import requires_db

pytestmark = []

FAKE_IMAGE_BYTES = b"not a real jpeg, just needs to hash consistently"


def _make_image(db_session, storage_url="https://agtrail.example.com/photo.jpg"):
    from app.models.farm import Farm
    from app.models.farmer import Farmer
    from app.models.image import FarmImage
    from app.models.visit import FarmVisit

    farmer = Farmer(full_name="Test Farmer", phone="080000000", state="Oyo", lga="Ibadan North")
    db_session.add(farmer)
    db_session.flush()
    farm = Farm(farmer_id=farmer.id, name="Test Farm")
    db_session.add(farm)
    db_session.flush()
    visit = FarmVisit(farm_id=farm.id)
    db_session.add(visit)
    db_session.flush()
    image = FarmImage(visit_id=visit.id, storage_url=storage_url)
    db_session.add(image)
    db_session.flush()
    return image


@requires_db
def test_archive_image_uploads_and_records_hash(db_session, monkeypatch):
    import app.services.image_archive as image_archive

    image = _make_image(db_session)
    db_session.commit()

    monkeypatch.setattr(image_archive, "fetch_bytes", lambda url, **k: (FAKE_IMAGE_BYTES, "image/jpeg"))
    uploads = []
    monkeypatch.setattr(
        image_archive, "upload_image_bytes",
        lambda data, sha, ct: uploads.append((data, sha, ct)) or f"gs://bucket/farm-images/{sha}",
    )

    did_archive = image_archive.archive_image(db_session, image)

    expected_hash = hashlib.sha256(FAKE_IMAGE_BYTES).hexdigest()
    assert did_archive is True
    assert image.content_sha256 == expected_hash
    assert image.archived_url == f"gs://bucket/farm-images/{expected_hash}"
    assert uploads == [(FAKE_IMAGE_BYTES, expected_hash, "image/jpeg")]


@requires_db
def test_archive_image_is_a_no_op_if_already_archived(db_session, monkeypatch):
    import app.services.image_archive as image_archive

    image = _make_image(db_session)
    image.archived_url = "gs://bucket/farm-images/already-there"
    image.content_sha256 = "already-there"
    db_session.commit()

    calls = []
    monkeypatch.setattr(image_archive, "fetch_bytes", lambda url, **k: calls.append(1) or (FAKE_IMAGE_BYTES, None))

    did_archive = image_archive.archive_image(db_session, image)

    assert did_archive is False
    assert calls == []
    assert image.archived_url == "gs://bucket/farm-images/already-there"


@requires_db
def test_archive_image_for_id_skips_missing_image(db_session, monkeypatch):
    import uuid

    import app.services.image_archive as image_archive

    monkeypatch.setattr(image_archive, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    calls = []
    monkeypatch.setattr(image_archive, "fetch_bytes", lambda url, **k: calls.append(1) or (FAKE_IMAGE_BYTES, None))

    image_archive.archive_image_for_id(uuid.uuid4())

    assert calls == []


def test_upload_image_bytes_skips_write_if_blob_already_exists(monkeypatch):
    import app.services.image_archive as image_archive

    class FakeBlob:
        def __init__(self):
            self.uploaded = False

        def exists(self):
            return True

        def upload_from_string(self, *a, **k):
            self.uploaded = True

    class FakeBucket:
        def __init__(self, blob):
            self._blob = blob

        def blob(self, name):
            return self._blob

    class FakeClient:
        def __init__(self, blob):
            self._blob = blob

        def bucket(self, name):
            return FakeBucket(self._blob)

    from app.core.config import settings

    fake_blob = FakeBlob()
    monkeypatch.setattr(settings, "S3_BUCKET", "")
    monkeypatch.setattr(image_archive, "_get_gcs_client", lambda: FakeClient(fake_blob))

    url = image_archive.upload_image_bytes(b"data", "abc123", "image/jpeg")

    assert fake_blob.uploaded is False
    assert url.endswith("farm-images/abc123")


@requires_db
def test_archive_image_refuses_a_non_image(db_session, monkeypatch):
    import app.services.image_archive as image_archive

    image = _make_image(db_session)
    db_session.commit()

    monkeypatch.setattr(image_archive, "fetch_bytes", lambda url, **k: (b"<html></html>", "text/html"))
    uploads = []
    monkeypatch.setattr(image_archive, "upload_image_bytes", lambda *a: uploads.append(a) or "gs://x")

    did_archive = image_archive.archive_image(db_session, image)

    assert did_archive is False
    assert uploads == []
    assert image.archived_url is None
    assert image.content_sha256 is None


# ---------------- which storage, and how it is called (no database needed) ----------------

import pytest
from botocore.exceptions import ClientError


class FakeS3:
    def __init__(self, existing=False, head_error="404"):
        self.existing, self.head_error, self.puts = existing, head_error, []

    def head_object(self, Bucket, Key):
        if self.existing:
            return {}
        raise ClientError({"Error": {"Code": self.head_error}}, "HeadObject")

    def put_object(self, **kwargs):
        self.puts.append(kwargs)


@pytest.fixture
def s3_settings(monkeypatch):
    import app.services.image_archive as image_archive
    from app.core.config import settings

    monkeypatch.setattr(settings, "S3_BUCKET", "aglens-images")
    monkeypatch.setattr(settings, "GCP_BUCKET_NAME", "")
    return image_archive


def test_the_s3_bucket_is_used_and_the_content_type_is_stored_with_the_file(s3_settings, monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(s3_settings, "_get_s3_client", lambda: fake)

    pointer = s3_settings.upload_image_bytes(b"photo", "abc123", "image/jpeg")

    assert pointer == "s3://aglens-images/farm-images/abc123"
    assert fake.puts == [{"Bucket": "aglens-images", "Key": "farm-images/abc123", "Body": b"photo", "ContentType": "image/jpeg"}]


def test_a_file_already_in_the_bucket_is_not_written_again(s3_settings, monkeypatch):
    fake = FakeS3(existing=True)
    monkeypatch.setattr(s3_settings, "_get_s3_client", lambda: fake)

    assert s3_settings.upload_image_bytes(b"photo", "abc123", None) == "s3://aglens-images/farm-images/abc123"
    assert fake.puts == []


def test_an_unknown_content_type_is_stored_as_a_plain_binary(s3_settings, monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(s3_settings, "_get_s3_client", lambda: fake)

    s3_settings.upload_image_bytes(b"photo", "abc123", None)

    assert fake.puts[0]["ContentType"] == "application/octet-stream"


def test_a_real_storage_error_is_raised_not_mistaken_for_a_missing_file(s3_settings, monkeypatch):
    monkeypatch.setattr(s3_settings, "_get_s3_client", lambda: FakeS3(head_error="AccessDenied"))

    with pytest.raises(ClientError):
        s3_settings.upload_image_bytes(b"photo", "abc123", "image/jpeg")


def test_the_s3_client_is_set_up_the_way_garage_needs(monkeypatch):
    import app.services.image_archive as image_archive
    from app.core.config import settings

    monkeypatch.setattr(settings, "S3_ENDPOINT", "https://s3.example.test")
    monkeypatch.setattr(settings, "S3_REGION", "garage")
    monkeypatch.setattr(settings, "S3_ACCESS_KEY", "key")
    monkeypatch.setattr(settings, "S3_SECRET_KEY", "secret")
    monkeypatch.setattr(image_archive, "_s3_client", None)

    client = image_archive._get_s3_client()

    assert client.meta.endpoint_url == "https://s3.example.test"
    assert client.meta.region_name == "garage"
    assert client.meta.config.s3["addressing_style"] == "path"
    monkeypatch.setattr(image_archive, "_s3_client", None)


def test_google_storage_is_only_the_fallback(monkeypatch):
    import app.services.image_archive as image_archive
    from app.core.config import settings

    class Blob:
        def exists(self):
            return False

        def upload_from_string(self, data, content_type):
            uploads.append((data, content_type))

    class Bucket:
        def blob(self, key):
            uploads.append(key)
            return Blob()

    class Client:
        def bucket(self, name):
            return Bucket()

    uploads = []
    monkeypatch.setattr(settings, "S3_BUCKET", "")
    monkeypatch.setattr(settings, "GCP_BUCKET_NAME", "old-bucket")
    monkeypatch.setattr(image_archive, "_get_gcs_client", lambda: Client())

    assert image_archive.upload_image_bytes(b"photo", "abc123", "image/png") == "gs://old-bucket/farm-images/abc123"
    assert uploads == ["farm-images/abc123", (b"photo", "image/png")]


def test_archiving_is_on_when_either_bucket_is_set_and_off_when_neither_is(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "S3_BUCKET", "")
    monkeypatch.setattr(settings, "GCP_BUCKET_NAME", "")
    assert settings.archive_enabled is False
    monkeypatch.setattr(settings, "S3_BUCKET", "aglens-images")
    assert settings.archive_enabled is True
    monkeypatch.setattr(settings, "S3_BUCKET", "")
    monkeypatch.setattr(settings, "GCP_BUCKET_NAME", "old-bucket")
    assert settings.archive_enabled is True


# ---------------- backfilling images that arrived before archiving was on ----------------

@requires_db
def test_backfill_queues_only_images_that_are_not_archived_and_not_already_queued(db_session, monkeypatch):
    import app.jobs.backfill_archive as job
    from app.core.config import settings
    from app.models.job import Job
    from app.services.job_queue import JOB_ARCHIVE_IMAGE, enqueue

    waiting = _make_image(db_session, "https://agtrail.example.com/a.jpg")
    done = _make_image(db_session, "https://agtrail.example.com/b.jpg")
    done.archived_url = "s3://aglens-images/farm-images/x"
    queued = _make_image(db_session, "https://agtrail.example.com/c.jpg")
    enqueue(db_session, JOB_ARCHIVE_IMAGE, {"image_id": str(queued.id)})
    db_session.commit()
    before = db_session.query(Job).filter(Job.job_type == JOB_ARCHIVE_IMAGE).count()

    monkeypatch.setattr(settings, "S3_BUCKET", "aglens-images")
    monkeypatch.setattr(job, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    assert job.backfill() == 1
    jobs = db_session.query(Job).filter(Job.job_type == JOB_ARCHIVE_IMAGE).all()
    assert len(jobs) == before + 1
    assert str(waiting.id) in {j.payload["image_id"] for j in jobs}
    assert job.backfill() == 0  # running again adds nothing


def test_backfill_does_nothing_without_a_bucket(monkeypatch):
    import app.jobs.backfill_archive as job
    from app.core.config import settings

    monkeypatch.setattr(settings, "S3_BUCKET", "")
    monkeypatch.setattr(settings, "GCP_BUCKET_NAME", "")

    assert job.backfill() == 0


def test_with_no_bucket_the_error_says_so_instead_of_asking_for_google_credentials(monkeypatch):
    import app.services.image_archive as image_archive
    from app.core.config import settings

    monkeypatch.setattr(settings, "S3_BUCKET", "")
    monkeypatch.setattr(settings, "GCP_BUCKET_NAME", "")

    with pytest.raises(RuntimeError, match="No storage bucket is configured"):
        image_archive.upload_image_bytes(b"photo", "abc123", "image/jpeg")
