"""add jobs table for the postgres-backed background queue

Revision ID: f1e2d3c4b5a6
Revises: a3f1c9d2e7b4
Create Date: 2026-09-29 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f1e2d3c4b5a6'
down_revision: Union[str, None] = 'a3f1c9d2e7b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # create_type=False: the type is created explicitly below, so
    # create_table's own column-type DDL doesn't try to create it again.
    job_status_enum = sa.Enum(
        'queued', 'running', 'succeeded', 'failed', 'dead',
        name='jobstatus', create_type=False,
    )
    job_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'jobs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('job_type', sa.String(length=50), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('status', job_status_enum, nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column('run_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('locked_by', sa.String(length=100), nullable=True),
        sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    # Matches the WHERE status = 'queued' AND run_at <= now() ... ORDER BY
    # run_at claim query the worker runs on every poll.
    op.create_index('ix_jobs_status_run_at', 'jobs', ['status', 'run_at'])


def downgrade() -> None:
    op.drop_index('ix_jobs_status_run_at', table_name='jobs')
    op.drop_table('jobs')
    sa.Enum(name='jobstatus').drop(op.get_bind(), checkfirst=True)
