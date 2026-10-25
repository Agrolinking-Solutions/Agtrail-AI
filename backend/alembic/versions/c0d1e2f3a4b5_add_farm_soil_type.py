"""add farms.soil_type

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-10-25 00:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "c0d1e2f3a4b5"
down_revision = "b9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("farms", sa.Column("soil_type", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("farms", "soil_type")
