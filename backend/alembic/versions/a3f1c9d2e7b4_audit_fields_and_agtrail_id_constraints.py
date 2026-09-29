"""keep prediction corrections separately, unique agtrail ids, non-unique farmer phone

Revision ID: a3f1c9d2e7b4
Revises: 66af208cacfa
Create Date: 2026-09-28 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a3f1c9d2e7b4'
down_revision: Union[str, None] = '66af208cacfa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Reuses the enum type created in the first migration.
farm_stage_enum = postgresql.ENUM(name='farmstage', create_type=False)


def upgrade() -> None:
    op.add_column('predictions', sa.Column('corrected_stage', farm_stage_enum, nullable=True))
    op.add_column('predictions', sa.Column('verified_by', sa.String(length=100), nullable=True))
    op.add_column('predictions', sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True))

    # Family members often share a phone, a unique phone made the webhook
    # fail for the second farmer. AgTrail's own ids are the real identity.
    op.drop_constraint('farmers_phone_key', 'farmers', type_='unique')

    # Fails if duplicates already exist. Find them first with:
    #   SELECT agtrail_farmer_id, count(*) FROM farmers GROUP BY 1 HAVING count(*) > 1;
    #   SELECT agtrail_farm_id, count(*) FROM farms GROUP BY 1 HAVING count(*) > 1;
    op.create_unique_constraint('uq_farmers_agtrail_farmer_id', 'farmers', ['agtrail_farmer_id'])
    op.create_unique_constraint('uq_farms_agtrail_farm_id', 'farms', ['agtrail_farm_id'])

    # Speeds up the latest-prediction and history lookups per farm.
    op.create_index('ix_predictions_farm_id_predicted_at', 'predictions', ['farm_id', 'predicted_at'])
    op.create_index('ix_farm_images_status', 'farm_images', ['status'])


def downgrade() -> None:
    op.drop_index('ix_farm_images_status', table_name='farm_images')
    op.drop_index('ix_predictions_farm_id_predicted_at', table_name='predictions')
    op.drop_constraint('uq_farms_agtrail_farm_id', 'farms', type_='unique')
    op.drop_constraint('uq_farmers_agtrail_farmer_id', 'farmers', type_='unique')
    op.create_unique_constraint('farmers_phone_key', 'farmers', ['phone'])
    op.drop_column('predictions', 'verified_at')
    op.drop_column('predictions', 'verified_by')
    op.drop_column('predictions', 'corrected_stage')
