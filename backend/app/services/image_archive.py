"""
Copies a farm image from AgTrail's own URL into our own storage, keyed by
the content's own sha256 hash. AgTrail's URLs aren't guaranteed to stay
valid forever, and a prediction tied to a vanished image can't be
re-verified, this closes that gap and naturally dedupes identical images
under one object.

The storage is our S3-compatible bucket on the VPS when S3_BUCKET is set,
otherwise Google Cloud Storage when GCP_BUCKET_NAME is set. What is stored in
archived_url is a pointer (s3://bucket/key or gs://bucket/key), never a link:
files are private, so a link that works for a while is made from the pointer
at the moment it is needed.
"""

import hashlib
import logging
import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.image import FarmImage
from app.services.safe_fetch import fetch_bytes

logger = logging.getLogger(__name__)

_s3_client = None
_gcs_client = None


def _get_s3_client():
    """Garage needs path-style addressing and its own region name."""
    global _s3_client
    if _s3_client is None:
        import boto3
        from botocore.config import Config

        _s3_client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT,
            region_name=settings.S3_REGION,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
        )
    return _s3_client


def _get_gcs_client():
    global _gcs_client
    if _gcs_client is None:
        from google.cloud import storage

        _gcs_client = storage.Client(project=settings.GCP_PROJECT_ID or None)
    return _gcs_client


def _upload_s3(data: bytes, key: str, content_type: str) -> str:
    from botocore.exceptions import ClientError

    client = _get_s3_client()
    try:
        client.head_object(Bucket=settings.S3_BUCKET, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchKey", "NotFound"):
            raise
        client.put_object(Bucket=settings.S3_BUCKET, Key=key, Body=data, ContentType=content_type)
    return f"s3://{settings.S3_BUCKET}/{key}"


def _upload_gcs(data: bytes, key: str, content_type: str) -> str:
    blob = _get_gcs_client().bucket(settings.GCP_BUCKET_NAME).blob(key)
    if not blob.exists():
        blob.upload_from_string(data, content_type=content_type)
    return f"gs://{settings.GCP_BUCKET_NAME}/{key}"


def upload_image_bytes(data: bytes, sha256_hex: str, content_type: str | None) -> str:
    """Uploads under a content-addressed path, so archiving the same image
    twice is a no-op write rather than a duplicate object. The content type is
    stored with the file so a link to it displays in a browser."""
    key = f"farm-images/{sha256_hex}"
    content_type = content_type or "application/octet-stream"
    if settings.S3_BUCKET:
        return _upload_s3(data, key, content_type)
    if settings.GCP_BUCKET_NAME:
        return _upload_gcs(data, key, content_type)
    raise RuntimeError(
        "No storage bucket is configured. Set S3_BUCKET and the other S3_ settings "
        "(or GCP_BUCKET_NAME) in the app's environment variables, then redeploy."
    )


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
