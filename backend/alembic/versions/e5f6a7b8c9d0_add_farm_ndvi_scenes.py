"""add farm_ndvi_scenes

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-24 00:00:00

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "farm_ndvi_scenes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "farm_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("farms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scene_date", sa.Date(), nullable=False),
        sa.Column("ndvi_mean", sa.Float(), nullable=True),
        sa.Column("polygon_pixels", sa.Integer(), nullable=False),
        sa.Column("clear_fraction", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("farm_id", "scene_date", name="uq_ndvi_scene_farm_date"),
    )
    op.create_index("ix_farm_ndvi_scenes_farm_id", "farm_ndvi_scenes", ["farm_id"])


def downgrade() -> None:
    op.drop_index("ix_farm_ndvi_scenes_farm_id", table_name="farm_ndvi_scenes")
    op.drop_table("farm_ndvi_scenes")
