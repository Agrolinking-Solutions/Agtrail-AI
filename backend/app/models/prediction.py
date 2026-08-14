import uuid
from datetime import datetime
from sqlalchemy import String, Float, Boolean, DateTime, ForeignKey, Enum, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.core.database import Base
import enum


class FarmStage(str, enum.Enum):
    land_preparation = "land_preparation"
    planting = "planting"
    fertilizer_application = "fertilizer_application"
    irrigation = "irrigation"
    weeding = "weeding"
    pest_control = "pest_control"
    pruning = "pruning"
    harvesting = "harvesting"
    unknown = "unknown"


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    image_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("farm_images.id", ondelete="CASCADE"), nullable=False
    )
    farm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("farms.id", ondelete="CASCADE"), nullable=False
    )
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    predicted_stage: Mapped[FarmStage] = mapped_column(
        Enum(FarmStage), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    raw_scores: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    predicted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    image: Mapped["FarmImage"] = relationship("FarmImage", back_populates="predictions")
    farm: Mapped["Farm"] = relationship("Farm", back_populates="predictions")
    sync_logs: Mapped[list["AgtrailSyncLog"]] = relationship(
        "AgtrailSyncLog", back_populates="prediction"
    )