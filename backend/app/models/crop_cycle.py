import uuid
from datetime import date, datetime
from sqlalchemy import String, Date, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base


class CropCycle(Base):
    """One growing season on one farm, as AgTrail reports it. Before this
    table existed the webhook read these details and threw them away."""

    __tablename__ = "crop_cycles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    farm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("farms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agtrail_cycle_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    # Exactly what AgTrail sent, which is free text typed by users.
    crop_raw: Mapped[str] = mapped_column(String(100), nullable=False)
    # Cleaned-up crop name from crop_aliases, when we recognise the text.
    crop_canonical: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # The Knowledge Base crop file to use, when that crop has one.
    crop_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    variety: Mapped[str | None] = mapped_column(String(200), nullable=True)
    planted_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    source: Mapped[str] = mapped_column(
        String(30), nullable=False, default="agtrail", server_default="agtrail"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    farm: Mapped["Farm"] = relationship("Farm", back_populates="crop_cycles")
    operations: Mapped[list["FarmOperation"]] = relationship(
        "FarmOperation", back_populates="crop_cycle"
    )

    def __repr__(self) -> str:
        return f"<CropCycle {self.agtrail_cycle_id} {self.crop_raw}>"
