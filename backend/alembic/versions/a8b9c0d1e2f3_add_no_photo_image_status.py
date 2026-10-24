"""add the no_photo image status

Revision ID: a8b9c0d1e2f3
Revises: f6a7b8c9d0e1
Create Date: 2026-10-23 00:00:00

AgTrail sends a stand-in link (.../uploads/no-image.jpg) when an operation is
logged without a photo. Those images are now recorded as no_photo and get no
archive or inference jobs. Rows already stored for such links are corrected once.
"""
from alembic import op


revision = "a8b9c0d1e2f3"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


# Rows already stored for a stand-in link that was never fetched or archived.
CORRECT_EXISTING_ROWS = (
    "UPDATE farm_images SET status = 'no_photo' "
    "WHERE status IN ('pending', 'failed') "
    "AND archived_url IS NULL "
    "AND storage_url ~* '/no-image\\.(jpe?g|png|webp|gif)$'"
)


def upgrade() -> None:
    # A new enum value cannot be used in the transaction that adds it.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE imagestatus ADD VALUE IF NOT EXISTS 'no_photo'")
    op.execute(CORRECT_EXISTING_ROWS)


def downgrade() -> None:
    # Postgres cannot drop one value from an enum. Put the rows back to what they
    # were before, and leave the unused value in place.
    op.execute("UPDATE farm_images SET status = 'failed' WHERE status = 'no_photo'")
