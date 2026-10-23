import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OperationSignal(Base):
    """What the Knowledge Base's rules say about one logged operation. Rebuilt by
    app.jobs.judge_operations, so the row always reflects the latest rules and the
    latest history of the cycle; nothing is edited by hand."""

    __tablename__ = "operation_signals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("farm_operations.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    farm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("farms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    crop_cycle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crop_cycles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # ok | warning | blocked | not_applicable | unknown (see app.services.signal_engine)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    kb_operation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{"rule_id", "severity", "message"}]
    issues: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # The Knowledge Base data version that produced this verdict.
    kb_version: Mapped[str | None] = mapped_column(String(60), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
