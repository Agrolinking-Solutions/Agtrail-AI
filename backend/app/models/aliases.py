from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

# Nothing here is final until a person has reviewed it.
UNVERIFIED = "UNVERIFIED"
REVIEWED = "reviewed"


class OperationTypeAlias(Base):
    """Translates the operation names AgTrail sends into the shared list in
    app.core.signal_categories. The key is the lower-cased name."""

    __tablename__ = "operation_type_aliases"

    logged_type: Mapped[str] = mapped_column(String(100), primary_key=True)
    signal_category: Mapped[str] = mapped_column(String(40), nullable=False)
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default=UNVERIFIED)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class CropAlias(Base):
    """Translates the crop text users type into AgTrail (spellings, typos,
    farm names) into one clean crop name, and into a Knowledge Base crop
    file when that crop has one. The key is the lower-cased text."""

    __tablename__ = "crop_aliases"

    alias: Mapped[str] = mapped_column(String(200), primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(100), nullable=False)
    kb_crop_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default=UNVERIFIED)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
