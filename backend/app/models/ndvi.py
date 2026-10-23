import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FarmNdviObservation(Base):
    __tablename__ = "farm_ndvi_observations"
    __table_args__ = (UniqueConstraint("farm_id", "period_start", name="uq_ndvi_farm_period"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    farm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("farms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    ndvi_mean: Mapped[float] = mapped_column(Float, nullable=False)
    valid_pixel_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FarmNdviScene(Base):
    """One satellite pass over a farm, with clouds removed per pixel. Alongside,
    not instead of, the 10-day farm_ndvi_observations."""

    __tablename__ = "farm_ndvi_scenes"
    __table_args__ = (UniqueConstraint("farm_id", "scene_date", name="uq_ndvi_scene_farm_date"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    farm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("farms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scene_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Null when the whole farm was under cloud that day.
    ndvi_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    polygon_pixels: Mapped[int] = mapped_column(Integer, nullable=False)
    clear_fraction: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
