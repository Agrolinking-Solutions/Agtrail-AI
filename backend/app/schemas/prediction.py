import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.prediction import FarmStage


class PredictionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    farm_id: uuid.UUID
    image_id: uuid.UUID
    model_version: str
    predicted_stage: FarmStage
    confidence: float
    predicted_at: datetime
    verified: bool


class VerifyPredictionRequest(BaseModel):
    verified: bool
    corrected_stage: FarmStage | None = None