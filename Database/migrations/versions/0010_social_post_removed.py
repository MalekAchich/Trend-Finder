"""our posts deleted on the platform are marked, not studied or reported again

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-10 14:20:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('social_posts', sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('social_posts', 'removed_at')
