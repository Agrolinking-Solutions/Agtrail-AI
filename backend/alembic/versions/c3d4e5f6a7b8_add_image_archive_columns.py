"""add image archive columns

Revision ID: c3d4e5f6a7b8
Revises: a7b8c9d0e1f2
Create Date: 2026-10-09 00:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "c3d4e5f6a7b8"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("farm_images", sa.Column("archived_url", sa.String(length=500), nullable=True))
    op.add_column("farm_images", sa.Column("content_sha256", sa.String(length=64), nullable=True))
    op.create_index("ix_farm_images_content_sha256", "farm_images", ["content_sha256"])


def downgrade() -> None:
    op.drop_index("ix_farm_images_content_sha256", table_name="farm_images")
    op.drop_column("farm_images", "content_sha256")
    op.drop_column("farm_images", "archived_url")
