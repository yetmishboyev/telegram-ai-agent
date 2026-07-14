"""drop unused is_whitelisted column

Revision ID: a1b2c3d4e5f6
Revises: 33f77c534df0
Create Date: 2026-07-14 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '33f77c534df0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # is_whitelisted hech qayerda o'qilmagan, faqat yozilgan (o'lik funksiya) —
    # tegishli endpoint/repo metodi bilan birga olib tashlandi.
    op.drop_column('telegram_users', 'is_whitelisted')


def downgrade() -> None:
    op.add_column(
        'telegram_users',
        sa.Column('is_whitelisted', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
