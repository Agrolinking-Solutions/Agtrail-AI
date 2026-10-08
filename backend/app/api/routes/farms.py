import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_api_key
from app.models.farm import Farm
from app.models.ndvi import FarmNdviObservation
from app.schemas.ndvi import NdviObservationOut

router = APIRouter(
    prefix="/farms",
    tags=["farms"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/{farm_id}/ndvi", response_model=list[NdviObservationOut])
def get_ndvi_trend(
    farm_id: uuid.UUID,
    limit: int = Query(default=52, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Oldest to newest, so a chart can plot it left to right without resorting."""
    if db.get(Farm, farm_id) is None:
        raise HTTPException(status_code=404, detail="Farm not found")

    observations = db.execute(
        select(FarmNdviObservation)
        .where(FarmNdviObservation.farm_id == farm_id)
        .order_by(FarmNdviObservation.period_start.desc())
        .limit(limit)
    ).scalars().all()

    return list(reversed(observations))
