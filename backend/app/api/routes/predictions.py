import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.prediction import Prediction
from app.schemas.prediction import PredictionOut, VerifyPredictionRequest

router = APIRouter(prefix="/predictions", tags=["predictions"])


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
def get_prediction_history(farm_id: uuid.UUID, db: Session = Depends(get_db)):
    predictions = db.execute(
        select(Prediction)
        .where(Prediction.farm_id == farm_id)
        .order_by(Prediction.predicted_at.desc())
    ).scalars().all()

    return list(predictions)


@router.post("/{prediction_id}/verify", response_model=PredictionOut)
def verify_prediction(
    prediction_id: uuid.UUID,
    body: VerifyPredictionRequest,
    db: Session = Depends(get_db),
):
    prediction = db.get(Prediction, prediction_id)

    if prediction is None:
        raise HTTPException(status_code=404, detail="Prediction not found")

    prediction.verified = body.verified

    if body.corrected_stage is not None:
        prediction.predicted_stage = body.corrected_stage

    db.commit()
    db.refresh(prediction)

    return prediction