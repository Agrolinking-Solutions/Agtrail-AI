"""add farm_ndvi_frames

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-10-26 00:00:00

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "farm_ndvi_frames",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("farm_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("farms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scene_date", sa.Date(), nullable=False),
        sa.Column("ndvi_mean", sa.Float(), nullable=True),
        sa.Column("clear_fraction", sa.Float(), nullable=False),
        sa.Column("image_pointer", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("farm_id", "scene_date", name="uq_ndvi_frame_farm_date"),
    )
    op.create_index("ix_farm_ndvi_frames_farm_id", "farm_ndvi_frames", ["farm_id"])


def downgrade() -> None:
    op.drop_index("ix_farm_ndvi_frames_farm_id", table_name="farm_ndvi_frames")
    op.drop_table("farm_ndvi_frames")
