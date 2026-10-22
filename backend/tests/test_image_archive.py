"""Tests for copying a farm image into our own storage, GCS calls mocked."""

import hashlib

from tests.conftest import requires_db

pytestmark = requires_db

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

    fake_blob = FakeBlob()
    monkeypatch.setattr(image_archive, "_get_client", lambda: FakeClient(fake_blob))

    url = image_archive.upload_image_bytes(b"data", "abc123", "image/jpeg")

    assert fake_blob.uploaded is False
    assert url.endswith("farm-images/abc123")


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
