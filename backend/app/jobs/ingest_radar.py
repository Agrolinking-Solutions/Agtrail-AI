"""
Sentinel-1 radar passes for every mapped farm. Radar is not stopped by cloud or night, so it gives a
reading on the days the optical satellite is blocked (most of the rainy season). It is a different kind
of measurement from NDVI: backscatter changes with crop growth, plant structure, soil moisture and the
state of the ground, so it is read as a trend, never as a single number.

    python -m app.jobs.ingest_radar            # the recent window (first run: a longer history)
    python -m app.jobs.ingest_radar --days 120 # a chosen window for every farm

Runs after the NDVI job in the daily task. A failure here is logged and never stops that job.
"""

import argparse
import logging
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.jobs.ingest_ndvi import _mapped_farms
from app.models.ndvi import FarmRadarScene
from app.services.sentinel import fetch_farm_radar, get_access_token

logger = logging.getLogger(__name__)

RECENT_DAYS = 30
FIRST_RUN_DAYS = 120


def _upsert(db, farm, scenes) -> int:
    for scene in scenes:
        stmt = insert(FarmRadarScene).values(
            farm_id=farm.id, scene_date=scene.scene_date, vv_db=scene.vv_db, vh_db=scene.vh_db,
            rvi=scene.rvi, polygon_pixels=scene.polygon_pixels,
        )
        db.execute(stmt.on_conflict_do_update(
            constraint="uq_radar_scene_farm_date",
            set_={"vv_db": stmt.excluded.vv_db, "vh_db": stmt.excluded.vh_db, "rvi": stmt.excluded.rvi,
                  "polygon_pixels": stmt.excluded.polygon_pixels},
        ))
    return len(scenes)


def ingest_radar(days: int | None = None, end: date | None = None) -> dict:
    end = end or date.today()
    summary = {"farms": 0, "scenes_written": 0, "farms_failed": 0}
    token = get_access_token()
    with SessionLocal() as db:
        farms = _mapped_farms(db)
        summary["farms"] = len(farms)
        have_history = set(db.execute(select(FarmRadarScene.farm_id).distinct()).scalars().all())
        for farm, boundary_geojson in farms:
            window = days or (RECENT_DAYS if farm.id in have_history else FIRST_RUN_DAYS)
            try:
                scenes = fetch_farm_radar(boundary_geojson, end - timedelta(days=window), end, token)
            except Exception as exc:
                summary["farms_failed"] += 1
                logger.warning("Radar fetch failed for farm %s: %s", farm.id, exc)
                continue
            summary["scenes_written"] += _upsert(db, farm, scenes)
            # Saved farm by farm, so a run that is cut off keeps what it fetched.
            db.commit()
    return summary


if __name__ == "__main__":
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=None, help="Window in days for every farm")
    args = parser.parse_args()
    logger.info("Radar ingestion finished: %s", ingest_radar(args.days))
