"""add subscriber_snapshots table

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-16 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'subscriber_snapshots',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('count', sa.Integer(), nullable=False),
        sa.Column('taken_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_subscriber_snapshots_taken_at', 'subscriber_snapshots', ['taken_at'])


def downgrade() -> None:
    op.drop_index('ix_subscriber_snapshots_taken_at', table_name='subscriber_snapshots')
    op.drop_table('subscriber_snapshots')
