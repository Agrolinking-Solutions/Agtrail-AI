"""
AgTrail's stand-in link for an operation logged without a photo: it is recorded as
having no photo and no archive or inference job is queued for it.
"""

import copy
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.services.image_placeholders import is_placeholder_image_url
from tests.conftest import load_payload, post_event, queued_image_ids, requires_db


@pytest.mark.parametrize(
    "url",
    [
        "https://cdn.agrolinking.com/uploads/no-image.jpg",
        "https://cdn.agrolinking.com/uploads/No-Image.PNG",
        "https://anywhere.example/a/b/no-image.webp?v=2",
    ],
)
def test_the_stand_in_links_are_recognised(url):
    assert is_placeholder_image_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://picsum.photos/400",
        "https://cdn.agrolinking.com/uploads/farm-12.jpg",
        "https://cdn.agrolinking.com/uploads/no-image-here.jpg",
        "https://cdn.agrolinking.com/no-image.jpg/real.jpg",
        "",
        None,
    ],
)
def test_real_photo_links_are_never_mistaken_for_the_stand_in(url):
    assert not is_placeholder_image_url(url)


@requires_db
def test_a_stand_in_image_is_kept_as_no_photo_with_no_jobs(client, db_session, monkeypatch):
    from app.core.config import settings
    from app.models.image import FarmImage, ImageStatus

    monkeypatch.setattr(settings, "S3_BUCKET", "aglens-images")
    payload = copy.deepcopy(load_payload("test_payload.json"))
    payload["image"]["url"] = "https://cdn.agrolinking.com/uploads/no-image.jpg"

    response = post_event(client, payload)

    assert response.status_code == 200, response.text
    image = db_session.execute(select(FarmImage)).scalar_one()
    assert image.status == ImageStatus.no_photo and image.farm_operation_id is not None
    assert image.source_payload_raw  # the webhook copy is kept, the replay tools rely on it
    assert queued_image_ids(db_session) == [] and queued_image_ids(db_session, job_type="archive_image") == []


@requires_db
def test_a_real_photo_is_still_queued_as_before(client, db_session, monkeypatch):
    from app.core.config import settings
    from app.models.image import FarmImage, ImageStatus

    monkeypatch.setattr(settings, "S3_BUCKET", "aglens-images")

    response = post_event(client, load_payload("test_payload.json"))

    assert response.status_code == 200, response.text
    image = db_session.execute(select(FarmImage)).scalar_one()
    assert image.status == ImageStatus.pending
    assert queued_image_ids(db_session) == [str(image.id)]
    assert queued_image_ids(db_session, job_type="archive_image") == [str(image.id)]


@requires_db
def test_the_archive_backfill_skips_images_with_no_photo(db_session, monkeypatch):
    import app.jobs.backfill_archive as job
    from app.core.config import settings
    from app.models.image import ImageStatus
    from tests.test_image_archive import _make_image

    real = _make_image(db_session, "https://agtrail.example.com/real.jpg")
    none = _make_image(db_session, "https://cdn.agrolinking.com/uploads/no-image.jpg")
    none.status = ImageStatus.no_photo
    db_session.commit()
    monkeypatch.setattr(settings, "S3_BUCKET", "aglens-images")
    monkeypatch.setattr(job, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    assert job._images_to_archive(db_session) == [real.id]


@requires_db
def test_the_one_off_correction_fixes_old_stand_in_rows_and_nothing_else(db_session):
    from app.models.image import ImageStatus
    from tests.test_image_archive import _make_image

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "a8b9c0d1e2f3_add_no_photo_image_status.py"
    spec = importlib.util.spec_from_file_location("no_photo_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    stand_in = _make_image(db_session, "https://cdn.agrolinking.com/uploads/no-image.jpg")
    stand_in.status = ImageStatus.failed
    pending = _make_image(db_session, "https://cdn.agrolinking.com/uploads/no-image.JPG")
    real = _make_image(db_session, "https://picsum.photos/400")
    real.status = ImageStatus.failed
    done = _make_image(db_session, "https://x.example/no-image.jpg")
    done.archived_url = "s3://aglens-images/farm-images/abc"
    db_session.commit()

    db_session.execute(text(migration.CORRECT_EXISTING_ROWS))
    db_session.commit()
    for image in (stand_in, pending, real, done):
        db_session.refresh(image)

    assert (stand_in.status, pending.status) == (ImageStatus.no_photo, ImageStatus.no_photo)
    assert real.status == ImageStatus.failed  # a real link that failed stays failed
    assert done.status == ImageStatus.pending  # already archived, so it was a real image
