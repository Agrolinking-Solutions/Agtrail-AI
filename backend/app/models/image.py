import uuid
from datetime import datetime
from sqlalchemy import String, Float, DateTime, ForeignKey, Enum, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.core.database import Base
import enum


class ImageSource(str, enum.Enum):
    agent_mobile = "agent_mobile"
    satellite = "satellite"
    agtrail_webhook = "agtrail_webhook"


class ImageStatus(str, enum.Enum):
    pending = "pending"
    processed = "processed"
    failed = "failed"


class FarmImage(Base):
    __tablename__ = "farm_images"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    visit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("farm_visits.id", ondelete="CASCADE"), nullable=False
    )
    storage_url: Mapped[str] = mapped_column(String(500), nullable=False)
    img_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    img_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[ImageSource] = mapped_column(
        Enum(ImageSource), default=ImageSource.agent_mobile, nullable=False
    )
    status: Mapped[ImageStatus] = mapped_column(
        Enum(ImageStatus), default=ImageStatus.pending, nullable=False, index=True
    )
    source_payload_raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    archived_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    visit: Mapped["FarmVisit"] = relationship("FarmVisit", back_populates="images")
    predictions: Mapped[list["Prediction"]] = relationship(
        "Prediction", back_populates="image"
    )