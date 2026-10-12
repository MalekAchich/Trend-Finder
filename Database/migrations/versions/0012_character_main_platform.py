"""a character's main platform: where it must succeed most (D-61)

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-13 00:10:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('characters', sa.Column('main_platform', sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column('characters', 'main_platform')
