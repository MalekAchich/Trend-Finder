"""where each of our posts' views come from (YouTube Analytics gives it per video)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-09 19:10:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('social_posts', sa.Column('audience', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('social_posts', 'audience')
