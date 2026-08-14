import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, Enum, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.core.database import Base
import enum


class SyncStatus(str, enum.Enum):
    pending = "pending"
    success = "success"
    failed = "failed"
    retrying = "retrying"


class SyncDirection(str, enum.Enum):
    inbound = "inbound"
    outbound = "outbound"


class AgtrailSyncLog(Base):
    __tablename__ = "agtrail_sync_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    prediction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("predictions.id", ondelete="SET NULL"), nullable=True
    )
    farm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("farms.id", ondelete="CASCADE"), nullable=False
    )
    agtrail_record_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    direction: Mapped[SyncDirection] = mapped_column(
        Enum(SyncDirection), default=SyncDirection.outbound, nullable=False
    )
    received_event_id: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)
    sync_status: Mapped[SyncStatus] = mapped_column(
        Enum(SyncStatus), default=SyncStatus.pending, nullable=False
    )
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    prediction: Mapped["Prediction"] = relationship("Prediction", back_populates="sync_logs")
    farm: Mapped["Farm"] = relationship("Farm", back_populates="sync_logs")