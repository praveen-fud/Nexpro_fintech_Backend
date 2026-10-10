"""add auth_sessions + refresh_tokens.session_id (idle timeout)

Revision ID: c4e8a1d2f6b7
Revises: b7d2f4a6c813
Create Date: 2026-10-10 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4e8a1d2f6b7'
down_revision: Union[str, Sequence[str], None] = 'b7d2f4a6c813'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'auth_sessions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_active_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_auth_sessions_user_id', 'auth_sessions', ['user_id'])
    with op.batch_alter_table('refresh_tokens') as batch:
        batch.add_column(sa.Column('session_id', sa.Uuid(), nullable=True))
        batch.create_index('ix_refresh_tokens_session_id', ['session_id'])
        batch.create_foreign_key('fk_refresh_tokens_session_id', 'auth_sessions', ['session_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('refresh_tokens') as batch:
        batch.drop_constraint('fk_refresh_tokens_session_id', type_='foreignkey')
        batch.drop_index('ix_refresh_tokens_session_id')
        batch.drop_column('session_id')
    op.drop_index('ix_auth_sessions_user_id', table_name='auth_sessions')
    op.drop_table('auth_sessions')
