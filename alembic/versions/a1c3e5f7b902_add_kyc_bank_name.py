"""add kyc bank_name

Revision ID: a1c3e5f7b902
Revises: 68ffd58b9389
Create Date: 2026-10-07 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c3e5f7b902'
down_revision: Union[str, Sequence[str], None] = '68ffd58b9389'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('kyc_profiles', sa.Column('bank_name', sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column('kyc_profiles', 'bank_name')
