import json
import logging
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.models.farm import Farm
from app.models.ndvi import FarmNdviObservation
from app.services.sentinel import default_window, fetch_farm_ndvi, get_access_token

logger = logging.getLogger(__name__)


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
        for farm, boundary_geojson in farms:
            try:
                periods = fetch_farm_ndvi(boundary_geojson, start, end, token)
            except Exception as exc:
                summary["farms_failed"] += 1
                logger.warning("NDVI fetch failed for farm %s: %s", farm.id, exc)
                continue
            summary["periods_written"] += _upsert_periods(db, farm, periods)
        db.commit()

    return summary


def main() -> None:
    configure_logging()
    summary = ingest_ndvi()
    logger.info("NDVI ingestion finished: %s", summary)


if __name__ == "__main__":
    main()
