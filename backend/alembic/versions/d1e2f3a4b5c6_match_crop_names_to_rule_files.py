"""point soybean spellings at the soybean rule file, add missing crop spellings, re-match stored cycles

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-10-26 00:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "d1e2f3a4b5c6"
down_revision = "c0d1e2f3a4b5"
branch_labels = None
depends_on = None

NOTE = "Added 26 Oct 2026 so this crop text reaches its Knowledge Base rule file. Needs review."

# (text as typed, lower-cased, clean name, Knowledge Base crop id). Only crops with a rule file and an unambiguous name.
NEW_ALIASES = [
    ("soybeans", "soybean", "soybean"),
    ("soyabeans", "soybean", "soybean"),
    ("soy bean", "soybean", "soybean"),
    ("sorghum", "sorghum", "sorghum"),
    ("guinea corn", "sorghum", "sorghum"),
    ("pearl millet", "pearl millet", "pearl_millet"),
    ("cashew", "cashew", "cashew"),
    ("onion", "onion", "onion"),
    ("sesame", "sesame", "sesame"),
    ("ginger", "ginger", "ginger"),
    ("cotton", "cotton", "cotton"),
    ("rubber", "rubber", "rubber"),
    ("oil palm", "oil palm", "oil_palm"),
    ("coffee", "coffee", "coffee"),
    ("tomato", "tomato", "tomato"),
]


def upgrade() -> None:
    conn = op.get_bind()
    # Soybean has a rule file now; the first spellings were saved when it had none.
    conn.execute(sa.text(
        "UPDATE crop_aliases SET kb_crop_id = 'soybean', note = :note WHERE canonical_name = 'soybean' AND kb_crop_id IS NULL"
    ), {"note": NOTE})
    for alias, canonical, kb_id in NEW_ALIASES:
        conn.execute(sa.text(
            "INSERT INTO crop_aliases (alias, canonical_name, kb_crop_id, review_status, note) "
            "VALUES (:a, :c, :k, 'UNVERIFIED', :n) ON CONFLICT (alias) DO NOTHING"
        ), {"a": alias, "c": canonical, "k": kb_id, "n": NOTE})
    # Cycles stored while the spelling had no rule file get their crop now, so their rules, price and estimate can run.
    conn.execute(sa.text(
        "UPDATE crop_cycles c SET crop_id = a.kb_crop_id, crop_canonical = a.canonical_name "
        "FROM crop_aliases a "
        "WHERE c.crop_id IS NULL AND a.kb_crop_id IS NOT NULL "
        "AND lower(regexp_replace(trim(c.crop_raw), '\\s+', ' ', 'g')) = a.alias"
    ))


def downgrade() -> None:
    # Data fix only: nothing to undo structurally. The re-matched cycles keep their crop.
    pass
