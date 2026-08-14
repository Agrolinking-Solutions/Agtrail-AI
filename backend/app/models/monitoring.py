import uuid
from datetime import date, datetime
from sqlalchemy import String, Date, DateTime, ForeignKey, Float, Boolean, Text, Enum, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base
import enum


class CycleType(str, enum.Enum):
    agent = "agent"
    satellite = "satellite"


class CycleStatus(str, enum.Enum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    missed = "missed"


class MonitoringCycle(Base):
    __tablename__ = "monitoring_cycles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    farm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("farms.id", ondelete="CASCADE"), nullable=False
    )
    cycle_start: Mapped[date] = mapped_column(Date, nullable=False)
    cycle_end: Mapped[date] = mapped_column(Date, nullable=False)
    cycle_type: Mapped[CycleType] = mapped_column(
        Enum(CycleType), default=CycleType.agent, nullable=False
    )
    status: Mapped[CycleStatus] = mapped_column(
        Enum(CycleStatus), default=CycleStatus.pending, nullable=False
    )

    farm: Mapped["Farm"] = relationship("Farm", back_populates="monitoring_cycles")
    visits: Mapped[list["FarmVisit"]] = relationship(
        "FarmVisit", back_populates="cycle"
    )
