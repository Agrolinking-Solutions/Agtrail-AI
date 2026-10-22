import uuid
from datetime import date, datetime
from sqlalchemy import String, Date, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base


class FarmOperation(Base):
    """One thing an agent logged on a farm, such as planting or weeding.
    Several photos can belong to the same operation."""

    __tablename__ = "farm_operations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    farm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("farms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    crop_cycle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crop_cycles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # AgTrail's own id. Optional in the webhook, and several NULLs are fine
    # in a unique column, so an operation without one is one row per photo.
    agtrail_operation_id: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)
    # Exactly what AgTrail sent.
    logged_type_raw: Mapped[str] = mapped_column(String(100), nullable=False)
    # Filled from operation_type_aliases. NULL means nobody has mapped it yet.
    signal_category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # The crop-specific Knowledge Base operation (for example weeding_1).
    # Filled in later, once the Knowledge Base mapping exists.
    kb_operation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # The date the agent logged, when AgTrail's webhook sends operation_date.
    # Until it does, this is the date of the earliest photo, in Nigerian time.
    operation_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(
        String(30), nullable=False, default="agtrail", server_default="agtrail"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    crop_cycle: Mapped["CropCycle"] = relationship("CropCycle", back_populates="operations")
    images: Mapped[list["FarmImage"]] = relationship("FarmImage", back_populates="operation")

    def __repr__(self) -> str:
        return f"<FarmOperation {self.logged_type_raw} {self.operation_date}>"
