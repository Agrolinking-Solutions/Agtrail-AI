import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_api_key
from app.models.prediction import Prediction
from app.schemas.prediction import PredictionOut, VerifyPredictionRequest

router = APIRouter(
    prefix="/predictions",
    tags=["predictions"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/{farm_id}", response_model=PredictionOut)
def get_latest_prediction(farm_id: uuid.UUID, db: Session = Depends(get_db)):
    prediction = db.execute(
        select(Prediction)
        .where(Prediction.farm_id == farm_id)
        .order_by(Prediction.predicted_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if prediction is None:
        raise HTTPException(status_code=404, detail="No predictions found for this farm")

    return prediction


@router.get("/{farm_id}/history", response_model=list[PredictionOut])
def get_prediction_history(
    farm_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    predictions = db.execute(
        select(Prediction)
        .where(Prediction.farm_id == farm_id)
        .order_by(Prediction.predicted_at.desc())
        .limit(limit)
        .offset(offset)
    ).scalars().all()

    return list(predictions)


@router.post("/{prediction_id}/verify", response_model=PredictionOut)
def verify_prediction(
    prediction_id: uuid.UUID,
    body: VerifyPredictionRequest,
    db: Session = Depends(get_db),
    caller: str = Depends(require_api_key),
):
    prediction = db.get(Prediction, prediction_id)

    if prediction is None:
        raise HTTPException(status_code=404, detail="Prediction not found")

    # The model's original output in predicted_stage is never overwritten,
    # corrections are kept alongside it for the audit trail and retraining.
    prediction.verified = body.verified
    prediction.corrected_stage = body.corrected_stage
    prediction.verified_by = caller
    prediction.verified_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(prediction)

    return prediction
