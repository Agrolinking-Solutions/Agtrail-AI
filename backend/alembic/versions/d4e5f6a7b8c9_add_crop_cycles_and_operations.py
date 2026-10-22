"""add crop cycles, farm operations and the alias tables

Saves what the AgTrail webhook used to read and discard (crop cycle details
and the operation an agent logged), plus two small translation tables whose
seed rows are all marked UNVERIFIED until a person reviews them.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-22 00:00:00

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


# What AgTrail's agents have logged so far (counts from production on
# 22 Oct 2026 are in docs/OPEN_QUESTIONS.md). "general" and anything the
# photo model calls irrigation or pruning have no dedicated category.
OPERATION_ALIASES = [
    ("land_preparation", "land_preparation", None),
    ("planting", "planting", None),
    ("weeding", "weeding", None),
    ("fertilizer", "fertilizer_application", "AgTrail says 'fertilizer'; the shared list says 'fertilizer_application'."),
    ("pest_control", "pest_control", None),
    ("harvesting", "harvesting", None),
    ("irrigation", "other", "No irrigation category in the shared list."),
    ("pruning", "other", "No pruning category in the shared list."),
    ("general", "other", "AgTrail sends 'general' when an operation has no category or type."),
]

# Crop text typed by users, seen in production on 22 Oct 2026. A NULL
# Knowledge Base id means that crop has no Knowledge Base file yet.
_NO_KB = "No Knowledge Base file for this crop yet."
CROP_ALIASES = [
    ("maize", "maize", "maize", None),
    ("maizw", "maize", "maize", "Typo."),
    ("maie", "maize", "maize", "Typo."),
    ("maize 2", "maize", "maize", "Looks like a farm label, not a crop."),
    ("soyabean", "soybean", None, _NO_KB),
    ("soybean", "soybean", None, _NO_KB),
    ("soya beans", "soybean", None, _NO_KB),
    ("soya bean", "soybean", None, _NO_KB),
    ("soy beans", "soybean", None, _NO_KB),
    ("soya", "soybean", None, _NO_KB),
    ("soyabean farm", "soybean", None, _NO_KB),
    ("amina soyabean e", "soybean", None, _NO_KB),
    ("rice", "rice", "rice", None),
    ("potato", "potato", None, _NO_KB),
    ("white potato", "potato", None, _NO_KB),
    ("groundnut", "groundnut", "groundnut", None),
    ("yam", "yam", "yam", None),
    ("yam nusery", "yam", "yam", "Typo and a nursery label."),
    ("cocoa", "cocoa", "cocoa", None),
    ("cassava", "cassava", "cassava", None),
    ("ofsp", "ofsp", "ofsp", None),
    ("orange fleshed sweet potatoes", "ofsp", "ofsp", None),
    ("cowpea", "cowpea", "cowpea", None),
    ("tomato farm", "tomato", "tomato", "Farm label, not a crop."),
    ("white beans", "white beans", None, "Could be cowpea or another bean. Needs an agronomist."),
    ("hayin alkali 2 - farm white beans", "white beans", None, "Farm label. Could be cowpea."),
    ("plantain", "plantain", None, _NO_KB),
    ("sugarcane", "sugarcane", None, _NO_KB),
    ("finger millet", "finger millet", None, _NO_KB),
]


def upgrade() -> None:
    op.create_table(
        "operation_type_aliases",
        sa.Column("logged_type", sa.String(length=100), primary_key=True),
        sa.Column("signal_category", sa.String(length=40), nullable=False),
        sa.Column("review_status", sa.String(length=20), nullable=False, server_default="UNVERIFIED"),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_table(
        "crop_aliases",
        sa.Column("alias", sa.String(length=200), primary_key=True),
        sa.Column("canonical_name", sa.String(length=100), nullable=False),
        sa.Column("kb_crop_id", sa.String(length=50), nullable=True),
        sa.Column("review_status", sa.String(length=20), nullable=False, server_default="UNVERIFIED"),
        sa.Column("note", sa.Text(), nullable=True),
    )

    op.create_table(
        "crop_cycles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("farm_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("farms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agtrail_cycle_id", sa.String(length=200), nullable=False),
        sa.Column("crop_raw", sa.String(length=100), nullable=False),
        sa.Column("crop_canonical", sa.String(length=100), nullable=True),
        sa.Column("crop_id", sa.String(length=50), nullable=True),
        sa.Column("variety", sa.String(length=200), nullable=True),
        sa.Column("planted_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False, server_default="agtrail"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("agtrail_cycle_id", name="uq_crop_cycles_agtrail_cycle_id"),
    )
    op.create_index("ix_crop_cycles_farm_id", "crop_cycles", ["farm_id"])

    op.create_table(
        "farm_operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("farm_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("farms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("crop_cycle_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("crop_cycles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agtrail_operation_id", sa.String(length=200), nullable=True),
        sa.Column("logged_type_raw", sa.String(length=100), nullable=False),
        sa.Column("signal_category", sa.String(length=40), nullable=True),
        sa.Column("kb_operation_id", sa.String(length=100), nullable=True),
        sa.Column("operation_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False, server_default="agtrail"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("agtrail_operation_id", name="uq_farm_operations_agtrail_operation_id"),
    )
    op.create_index("ix_farm_operations_farm_id", "farm_operations", ["farm_id"])
    op.create_index("ix_farm_operations_crop_cycle_id", "farm_operations", ["crop_cycle_id"])

    op.add_column(
        "farm_images",
        sa.Column(
            "farm_operation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("farm_operations.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_farm_images_farm_operation_id", "farm_images", ["farm_operation_id"])

    op.add_column("farms", sa.Column("source", sa.String(length=30), nullable=False, server_default="agtrail"))
    op.add_column("farms", sa.Column("owner_client_id", sa.String(length=100), nullable=True))

    operation_aliases = sa.table(
        "operation_type_aliases",
        sa.column("logged_type", sa.String),
        sa.column("signal_category", sa.String),
        sa.column("note", sa.Text),
    )
    op.bulk_insert(
        operation_aliases,
        [{"logged_type": t, "signal_category": c, "note": n} for t, c, n in OPERATION_ALIASES],
    )
    crop_aliases = sa.table(
        "crop_aliases",
        sa.column("alias", sa.String),
        sa.column("canonical_name", sa.String),
        sa.column("kb_crop_id", sa.String),
        sa.column("note", sa.Text),
    )
    op.bulk_insert(
        crop_aliases,
        [{"alias": a, "canonical_name": c, "kb_crop_id": k, "note": n} for a, c, k, n in CROP_ALIASES],
    )


def downgrade() -> None:
    op.drop_column("farms", "owner_client_id")
    op.drop_column("farms", "source")
    op.drop_index("ix_farm_images_farm_operation_id", table_name="farm_images")
    op.drop_column("farm_images", "farm_operation_id")
    op.drop_index("ix_farm_operations_crop_cycle_id", table_name="farm_operations")
    op.drop_index("ix_farm_operations_farm_id", table_name="farm_operations")
    op.drop_table("farm_operations")
    op.drop_index("ix_crop_cycles_farm_id", table_name="crop_cycles")
    op.drop_table("crop_cycles")
    op.drop_table("crop_aliases")
    op.drop_table("operation_type_aliases")
