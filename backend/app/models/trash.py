import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TrashedRecord(Base):
    """A farm taken out of the live tables with everything attached to it, kept whole so
    it can be shown to the team or put back. Nothing in the app reads this table."""

    __tablename__ = "trashed_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # {table name: [rows]}, every row as plain JSON (ids and dates as text, shapes as WKT).
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trashed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
