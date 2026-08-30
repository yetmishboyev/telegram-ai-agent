"""add notes table (ikkinchi miya)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-30 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notes',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('kind', sa.String(length=16), server_default='fikr', nullable=False),
        sa.Column('title', sa.String(length=256), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('source_url', sa.String(length=1024), nullable=True),
        sa.Column('source_kind', sa.String(length=16), server_default='matn', nullable=False),
        sa.Column('vector_id', sa.String(length=128), nullable=True),
        sa.Column('access_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_touched', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('pinned', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_notes_kind', 'notes', ['kind'])
    op.create_index('ix_notes_last_touched', 'notes', ['last_touched'])
    op.create_index('ix_notes_created_at', 'notes', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_notes_created_at', table_name='notes')
    op.drop_index('ix_notes_last_touched', table_name='notes')
    op.drop_index('ix_notes_kind', table_name='notes')
    op.drop_table('notes')
