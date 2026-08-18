"""add category column to channel_posts

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('channel_posts', sa.Column('category', sa.String(length=32), nullable=True))
    op.create_index('ix_channel_posts_category', 'channel_posts', ['category'])


def downgrade() -> None:
    op.drop_index('ix_channel_posts_category', table_name='channel_posts')
    op.drop_column('channel_posts', 'category')
