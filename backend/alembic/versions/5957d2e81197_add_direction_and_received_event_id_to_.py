"""add direction and received event id to sync log

Revision ID: 5957d2e81197
Revises: c7dbaabeceb0
Create Date: 2026-08-08 11:14:52.708427

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '5957d2e81197'
down_revision: Union[str, None] = 'c7dbaabeceb0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    sync_direction_enum = sa.Enum('inbound', 'outbound', name='syncdirection')
    sync_direction_enum.create(op.get_bind(), checkfirst=True)

    op.add_column('agtrail_sync_log', sa.Column('direction', sync_direction_enum, nullable=False))
    op.add_column('agtrail_sync_log', sa.Column('received_event_id', sa.String(length=200), nullable=True))
    op.create_unique_constraint('uq_agtrail_sync_log_received_event_id', 'agtrail_sync_log', ['received_event_id'])


def downgrade() -> None:
    op.drop_constraint('uq_agtrail_sync_log_received_event_id', 'agtrail_sync_log', type_='unique')
    op.drop_column('agtrail_sync_log', 'received_event_id')
    op.drop_column('agtrail_sync_log', 'direction')

    sync_direction_enum = sa.Enum('inbound', 'outbound', name='syncdirection')
    sync_direction_enum.drop(op.get_bind(), checkfirst=True)