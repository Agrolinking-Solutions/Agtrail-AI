"""add operation_signals

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-23 00:00:00

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operation_signals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "operation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("farm_operations.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "farm_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("farms.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "crop_cycle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("crop_cycles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("kb_operation_id", sa.String(100), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("issues", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("kb_version", sa.String(60), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_operation_signals_farm_id", "operation_signals", ["farm_id"])
    op.create_index("ix_operation_signals_crop_cycle_id", "operation_signals", ["crop_cycle_id"])


def downgrade() -> None:
    op.drop_index("ix_operation_signals_crop_cycle_id", table_name="operation_signals")
    op.drop_index("ix_operation_signals_farm_id", table_name="operation_signals")
    op.drop_table("operation_signals")
