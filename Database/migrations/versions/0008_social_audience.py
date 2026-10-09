"""our channels' audience (countries, ages, genders) and account insights, as the platforms last gave them

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-09 16:30:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column('social_channels', sa.Column('audience', JSONB, nullable=True))
    op.add_column('social_channels', sa.Column('account_insights', JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column('social_channels', 'account_insights')
    op.drop_column('social_channels', 'audience')
