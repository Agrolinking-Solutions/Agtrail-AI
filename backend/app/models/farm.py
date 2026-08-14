import uuid
from datetime import datetime
from sqlalchemy import String, Float, DateTime, ForeignKey, func, Enum
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
    commodity: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[FarmStatus] = mapped_column(
        Enum(FarmStatus), default=FarmStatus.active, nullable=False
    )
    agtrail_farm_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    farmer: Mapped["Farmer"] = relationship("Farmer", back_populates="farms")
    visits: Mapped[list["FarmVisit"]] = relationship("FarmVisit", back_populates="farm")
    monitoring_cycles: Mapped[list["MonitoringCycle"]] = relationship(
        "MonitoringCycle", back_populates="farm"
    )
    predictions: Mapped[list["Prediction"]] = relationship(
        "Prediction", back_populates="farm"
    )
    sync_logs: Mapped[list["AgtrailSyncLog"]] = relationship(
        "AgtrailSyncLog", back_populates="farm"
    )

    def __repr__(self) -> str:
        return f"<Farm {self.name} ({self.commodity})>"
