import uuid
from datetime import datetime
from sqlalchemy import String, Float, Boolean, DateTime, ForeignKey, func, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from geoalchemy2 import Geometry
from app.core.database import Base
import enum


class FarmStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"
    suspended = "suspended"


class Farm(Base):
    __tablename__ = "farms"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    farmer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("farmers.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    area_hectares: Mapped[float | None] = mapped_column(Float, nullable=True)
    coordinates: Mapped[object | None] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326), nullable=True
    )
    boundary: Mapped[object | None] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=4326), nullable=True
    )
    boundary_mapped: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    boundary_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    commodity: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[FarmStatus] = mapped_column(
        Enum(FarmStatus), default=FarmStatus.active, nullable=False
    )
    agtrail_farm_id: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)
    # Where the farm came from: "agtrail" today, later "aglens" or "api_client".
    source: Mapped[str] = mapped_column(
        String(30), nullable=False, default="agtrail", server_default="agtrail"
    )
    # Which paying API client owns the farm. NULL for AgTrail's own farms.
    owner_client_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    farmer: Mapped["Farmer"] = relationship("Farmer", back_populates="farms")
    visits: Mapped[list["FarmVisit"]] = relationship("FarmVisit", back_populates="farm")
    monitoring_cycles: Mapped[list["MonitoringCycle"]] = relationship(
        "MonitoringCycle", back_populates="farm"
    )
    crop_cycles: Mapped[list["CropCycle"]] = relationship("CropCycle", back_populates="farm")
    predictions: Mapped[list["Prediction"]] = relationship(
        "Prediction", back_populates="farm"
    )
    sync_logs: Mapped[list["AgtrailSyncLog"]] = relationship(
        "AgtrailSyncLog", back_populates="farm"
    )

    def __repr__(self) -> str:
        return f"<Farm {self.name} ({self.commodity})>"
