"""
Copies a farm image from AgTrail's own URL into our GCS bucket, keyed by
the content's own sha256 hash. AgTrail's URLs aren't guaranteed to stay
valid forever, and a prediction tied to a vanished image can't be
re-verified, this closes that gap and naturally dedupes identical images
under one object.
"""

import hashlib
import logging
import uuid

from google.cloud import storage
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.image import FarmImage
from app.services.safe_fetch import fetch_bytes

logger = logging.getLogger(__name__)

_client: storage.Client | None = None


def _get_client() -> storage.Client:
    global _client
    if _client is None:
        _client = storage.Client(project=settings.GCP_PROJECT_ID or None)
    return _client


def upload_image_bytes(data: bytes, sha256_hex: str, content_type: str | None) -> str:
    """Uploads under a content-addressed path, so archiving the same image
    twice is a no-op write rather than a duplicate object."""
    bucket = _get_client().bucket(settings.GCP_BUCKET_NAME)
    blob = bucket.blob(f"farm-images/{sha256_hex}")
    if not blob.exists():
        blob.upload_from_string(data, content_type=content_type or "application/octet-stream")
    return f"gs://{settings.GCP_BUCKET_NAME}/farm-images/{sha256_hex}"


def archive_image(db: Session, image: FarmImage) -> bool:
    """Returns True if this call did the archiving, False if it was
    already archived. Safe to call again on an already-archived image."""
    if image.archived_url:
        return False

    data, content_type = fetch_bytes(image.storage_url)
    # Inference decodes the file as an image, so a non-image never gets a
    # prediction. Don't keep one in our bucket either.
    if content_type and not content_type.lower().startswith("image/"):
        logger.warning(
            "Image %s is served as %r, not an image, skipping archive.", image.id, content_type
        )
        return False
    sha256_hex = hashlib.sha256(data).hexdigest()

    image.archived_url = upload_image_bytes(data, sha256_hex, content_type)
    image.content_sha256 = sha256_hex
    db.commit()
    return True


def archive_image_for_id(image_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        image = db.get(FarmImage, image_id)
        if image is None:
            logger.warning("Image %s not found, skipping archive.", image_id)
            return
        archive_image(db, image)
    finally:
        db.close()
