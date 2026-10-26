"""
Keeps one picture per clear satellite pass over a farm, so its changes can be played back as a timelapse.

A frame is the NDVI picture of one pass with cloud pixels transparent, stored in our own bucket. Only
passes where at least half the farm was clear get a frame (the same rule as the crop health reading),
and a frame is made once: it is never replaced. Making frames is secondary work: a failure is logged
and skipped, and never stops the satellite run that found the pass.
"""

import logging
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.ndvi import FarmNdviFrame, FarmNdviScene
from app.services.image_archive import _get_s3_client, _upload_s3
from app.services.sentinel import fetch_farm_frame

logger = logging.getLogger(__name__)

MIN_CLEAR_FRACTION = 0.5
MAX_FRAMES_PER_FARM_PER_RUN = 5


def frames_enabled() -> bool:
    return bool(settings.S3_BUCKET)


def frame_key(farm_id: uuid.UUID, scene_date: date) -> str:
    return f"ndvi-frames/{farm_id}/{scene_date.isoformat()}.png"


def render_png(boundary_geojson: dict, scene_date: date, token: str) -> bytes:
    return fetch_farm_frame(boundary_geojson, scene_date, token)


def store_png(data: bytes, key: str) -> str:
    return _upload_s3(data, key, "image/png")


def read_png(pointer: str) -> bytes:
    bucket, _, key = pointer.removeprefix("s3://").partition("/")
    return _get_s3_client().get_object(Bucket=bucket, Key=key)["Body"].read()


def usable(scene: FarmNdviScene) -> bool:
    return scene.ndvi_mean is not None and scene.clear_fraction >= MIN_CLEAR_FRACTION


def save_missing_frames(db: Session, farm, boundary_geojson: dict, token: str, limit: int | None = MAX_FRAMES_PER_FARM_PER_RUN) -> dict:
    """Makes the frames this farm does not have yet, newest first. Returns how many were saved and failed."""
    result = {"saved": 0, "failed": 0}
    if not frames_enabled():
        return result
    have = set(db.scalars(select(FarmNdviFrame.scene_date).where(FarmNdviFrame.farm_id == farm.id)).all())
    scenes = db.scalars(
        select(FarmNdviScene).where(FarmNdviScene.farm_id == farm.id).order_by(FarmNdviScene.scene_date.desc())
    ).all()
    for scene in scenes:
        if limit is not None and result["saved"] >= limit:
            break
        if scene.scene_date in have or not usable(scene):
            continue
        try:
            pointer = store_png(render_png(boundary_geojson, scene.scene_date, token), frame_key(farm.id, scene.scene_date))
        except Exception as exc:
            result["failed"] += 1
            logger.warning("Frame for farm %s on %s failed: %s", farm.id, scene.scene_date, exc)
            continue
        db.add(FarmNdviFrame(
            farm_id=farm.id, scene_date=scene.scene_date, ndvi_mean=scene.ndvi_mean,
            clear_fraction=scene.clear_fraction, image_pointer=pointer,
        ))
        db.commit()
        result["saved"] += 1
    return result
