"""
Makes the timelapse pictures for passes that were stored as numbers before frames were kept: one NDVI
picture for every clear pass (at least half the farm clear) that has none yet. Safe to run again, and
safe to stop: each frame is saved as it is made.

    python -m app.jobs.backfill_ndvi_frames              # every farm
    python -m app.jobs.backfill_ndvi_frames --limit 200  # at most 200 frames this run

Usage from Makefile: make backfill-ndvi-frames
"""

import argparse
import logging

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.jobs.ingest_ndvi import _mapped_farms
from app.services.ndvi_frames import check_storage, frames_enabled, save_missing_frames
from app.services.sentinel import get_access_token

logger = logging.getLogger(__name__)


def backfill_ndvi_frames(limit: int | None = None) -> dict:
    summary = {"farms": 0, "frames_saved": 0, "frames_failed": 0}
    if not frames_enabled():
        logger.error("No storage bucket is configured (S3_BUCKET), so frames cannot be kept.")
        return summary
    problem = check_storage()
    if problem:
        logger.error("Nothing was rendered. %s", problem)
        summary["storage_problem"] = problem
        return summary
    token = get_access_token()
    with SessionLocal() as db:
        farms = _mapped_farms(db)
        for farm, boundary_geojson in farms:
            if limit is not None and summary["frames_saved"] >= limit:
                break
            left = None if limit is None else limit - summary["frames_saved"]
            result = save_missing_frames(db, farm, boundary_geojson, token, limit=left)
            summary["farms"] += 1
            summary["frames_saved"] += result["saved"]
            summary["frames_failed"] += result["failed"]
            # Failing across several farms with nothing saved means something is wrong; stop spending renders.
            if summary["frames_saved"] == 0 and summary["frames_failed"] >= 6:
                logger.error("Stopping: %d frames failed and none saved.", summary["frames_failed"])
                break
    return summary


if __name__ == "__main__":
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Save at most this many frames in this run")
    args = parser.parse_args()
    logger.info("NDVI frame backfill finished: %s", backfill_ndvi_frames(args.limit))
