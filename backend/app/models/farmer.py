import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base


class Farmer(Base):
    __tablename__ = "farmers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    lga: Mapped[str] = mapped_column(String(100), nullable=False)
    agtrail_farmer_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    farms: Mapped[list["Farm"]] = relationship("Farm", back_populates="farmer")

    def __repr__(self) -> str:
        return f"<Farmer {self.full_name} ({self.phone})>"
