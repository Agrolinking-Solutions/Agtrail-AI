import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
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


class FarmNdviFrame(Base):
    """The picture of one clear satellite pass over a farm, kept in our bucket so the farm's changes
    can be played back as a timelapse. The scene's numbers stay in farm_ndvi_scenes."""

    __tablename__ = "farm_ndvi_frames"
    __table_args__ = (UniqueConstraint("farm_id", "scene_date", name="uq_ndvi_frame_farm_date"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    farm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("farms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scene_date: Mapped[date] = mapped_column(Date, nullable=False)
    ndvi_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    clear_fraction: Mapped[float] = mapped_column(Float, nullable=False)
    # s3://bucket/key, never a link: the files are private and are served through the API.
    image_pointer: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FarmRadarScene(Base):
    """One Sentinel-1 radar pass over a farm. Radar is not stopped by cloud, so it fills the gaps the
    optical satellite leaves in the rains. Backscatter in decibels; rvi is the radar vegetation index."""

    __tablename__ = "farm_radar_scenes"
    __table_args__ = (UniqueConstraint("farm_id", "scene_date", name="uq_radar_scene_farm_date"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    farm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("farms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scene_date: Mapped[date] = mapped_column(Date, nullable=False)
    vv_db: Mapped[float] = mapped_column(Float, nullable=False)
    vh_db: Mapped[float] = mapped_column(Float, nullable=False)
    rvi: Mapped[float] = mapped_column(Float, nullable=False)
    polygon_pixels: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
