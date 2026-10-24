import json
import logging
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.models.farm import Farm
from app.models.ndvi import FarmNdviObservation, FarmNdviScene
from app.services.sentinel import default_window, fetch_farm_ndvi, fetch_farm_scenes, get_access_token

logger = logging.getLogger(__name__)

# Above this, a farm is unusually large for a smallholder plot and worth a
# second look, this caught a boundary reporting 832 ha against AgTrail's
# typical farms (most under 1 ha).
LARGE_FARM_HECTARES = 100


PROGRESS_EVERY = 25


def _progress(what: str, done: int, total: int) -> None:
    if done % PROGRESS_EVERY == 0 or done == total:
        logger.info("%s: %d of %d farms done.", what, done, total)


def _mapped_farms(db: Session) -> list[tuple[Farm, dict]]:
    rows = db.execute(
        select(Farm, func.ST_AsGeoJSON(Farm.boundary))
        .where(Farm.boundary_mapped.is_(True), Farm.boundary.is_not(None))
    ).all()
    return [(farm, json.loads(geojson)) for farm, geojson in rows]


def _upsert_periods(db: Session, farm: Farm, periods) -> int:
    for period in periods:
        stmt = insert(FarmNdviObservation).values(
            farm_id=farm.id,
            period_start=period.period_start,
            period_end=period.period_end,
            ndvi_mean=period.ndvi_mean,
            valid_pixel_count=period.valid_pixel_count,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_ndvi_farm_period",
            set_={
                "period_end": stmt.excluded.period_end,
                "ndvi_mean": stmt.excluded.ndvi_mean,
                "valid_pixel_count": stmt.excluded.valid_pixel_count,
            },
        )
        db.execute(stmt)
    return len(periods)


def ingest_ndvi(start: date | None = None, end: date | None = None) -> dict:
    if start is None or end is None:
        start, end = default_window()

    summary = {"farms": 0, "periods_written": 0, "farms_failed": 0}
    token = get_access_token()

    with SessionLocal() as db:
        farms = _mapped_farms(db)
        summary["farms"] = len(farms)
        for index, (farm, boundary_geojson) in enumerate(farms, start=1):
            if farm.area_hectares and farm.area_hectares > LARGE_FARM_HECTARES:
                logger.warning(
                    "Farm %s (agtrail_farm_id %s) is %.1f ha, above the %d ha smallholder "
                    "range, worth confirming the boundary with AgTrail.",
                    farm.id, farm.agtrail_farm_id, farm.area_hectares, LARGE_FARM_HECTARES,
                )
            try:
                periods = fetch_farm_ndvi(boundary_geojson, start, end, token)
            except Exception as exc:
                summary["farms_failed"] += 1
                logger.warning("NDVI fetch failed for farm %s: %s", farm.id, exc)
                continue
            if not periods:
                logger.warning(
                    "No NDVI periods returned for farm %s (agtrail_farm_id %s, %s ha).",
                    farm.id, farm.agtrail_farm_id, farm.area_hectares,
                )
            summary["periods_written"] += _upsert_periods(db, farm, periods)
            # Saved farm by farm: a run that is cut off keeps what it already fetched.
            db.commit()
            _progress("10-day NDVI", index, len(farms))

    return summary


def _upsert_scenes(db: Session, farm: Farm, scenes) -> int:
    for scene in scenes:
        stmt = insert(FarmNdviScene).values(
            farm_id=farm.id,
            scene_date=scene.scene_date,
            ndvi_mean=scene.ndvi_mean,
            polygon_pixels=scene.polygon_pixels,
            clear_fraction=scene.clear_fraction,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_ndvi_scene_farm_date",
            set_={
                "ndvi_mean": stmt.excluded.ndvi_mean,
                "polygon_pixels": stmt.excluded.polygon_pixels,
                "clear_fraction": stmt.excluded.clear_fraction,
            },
        )
        db.execute(stmt)
    return len(scenes)


# A daily run only needs the recent passes: older ones are already stored and
# Copernicus finishes processing a pass within days. A farm with no passes yet
# (newly mapped) gets a longer first window to build its history.
RECENT_DAYS = 20
FIRST_RUN_DAYS = 90


def ingest_scenes(start: date | None = None, end: date | None = None) -> dict:
    """Per-pass NDVI with clouds removed pixel by pixel. Re-running a window
    refreshes it, so the last pass or two can still be corrected as Copernicus
    finishes processing them. With no dates given, each farm gets the recent
    window, or the longer first one if it has no passes stored yet."""
    explicit = start is not None and end is not None
    end = end or date.today()

    summary = {"farms": 0, "scenes_written": 0, "farms_failed": 0}
    token = get_access_token()

    with SessionLocal() as db:
        farms = _mapped_farms(db)
        summary["farms"] = len(farms)
        have_history = set(db.execute(select(FarmNdviScene.farm_id).distinct()).scalars().all())
        for index, (farm, boundary_geojson) in enumerate(farms, start=1):
            if not explicit:
                days = RECENT_DAYS if farm.id in have_history else FIRST_RUN_DAYS
                start = end - timedelta(days=days)
            try:
                scenes = fetch_farm_scenes(boundary_geojson, start, end, token)
            except Exception as exc:
                summary["farms_failed"] += 1
                logger.warning("Scene fetch failed for farm %s: %s", farm.id, exc)
                continue
            summary["scenes_written"] += _upsert_scenes(db, farm, scenes)
            # Saved farm by farm: with hundreds of farms a run takes a long time, and
            # one that is cut off must keep what it already fetched.
            db.commit()
            _progress("Scenes", index, len(farms))

    return summary


def main() -> None:
    configure_logging()
    # The per-pass data first: crop health and the farm summary read it.
    scene_summary = ingest_scenes()
    logger.info("NDVI scene ingestion finished: %s", scene_summary)
    summary = ingest_ndvi()
    logger.info("NDVI ingestion finished: %s", summary)


if __name__ == "__main__":
    main()
