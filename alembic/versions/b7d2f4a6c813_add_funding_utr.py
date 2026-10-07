"""add funding_requests.utr

Revision ID: b7d2f4a6c813
Revises: a1c3e5f7b902
Create Date: 2026-10-07 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7d2f4a6c813'
down_revision: Union[str, Sequence[str], None] = 'a1c3e5f7b902'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('funding_requests', sa.Column('utr', sa.String(length=32), nullable=True))
    op.create_index('ix_funding_requests_utr', 'funding_requests', ['utr'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_funding_requests_utr', table_name='funding_requests')
    op.drop_column('funding_requests', 'utr')
