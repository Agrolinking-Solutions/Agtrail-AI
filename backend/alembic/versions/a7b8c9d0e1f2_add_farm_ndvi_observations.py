"""add farm_ndvi_observations

Revision ID: a7b8c9d0e1f2
Revises: f1e2d3c4b5a6
Create Date: 2026-10-03 00:00:00

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a7b8c9d0e1f2"
down_revision = "f1e2d3c4b5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "farm_ndvi_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "farm_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("farms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("ndvi_mean", sa.Float(), nullable=False),
        sa.Column("valid_pixel_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("farm_id", "period_start", name="uq_ndvi_farm_period"),
    )
    op.create_index(
        "ix_farm_ndvi_observations_farm_id",
        "farm_ndvi_observations",
        ["farm_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_farm_ndvi_observations_farm_id", table_name="farm_ndvi_observations")
    op.drop_table("farm_ndvi_observations")
