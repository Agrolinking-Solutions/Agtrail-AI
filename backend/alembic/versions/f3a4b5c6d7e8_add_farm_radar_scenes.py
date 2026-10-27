"""add farm_radar_scenes

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-10-27 00:00:00

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "f3a4b5c6d7e8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "farm_radar_scenes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("farm_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("farms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scene_date", sa.Date(), nullable=False),
        sa.Column("vv_db", sa.Float(), nullable=False),
        sa.Column("vh_db", sa.Float(), nullable=False),
        sa.Column("rvi", sa.Float(), nullable=False),
        sa.Column("polygon_pixels", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("farm_id", "scene_date", name="uq_radar_scene_farm_date"),
    )
    op.create_index("ix_farm_radar_scenes_farm_id", "farm_radar_scenes", ["farm_id"])


def downgrade() -> None:
    op.drop_index("ix_farm_radar_scenes_farm_id", table_name="farm_radar_scenes")
    op.drop_table("farm_radar_scenes")
