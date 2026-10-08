"""our own posting channels, their posts and numbers over time (Plan 9)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-08 20:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table('social_channels',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('character_id', sa.UUID(), nullable=False),
    sa.Column('platform', sa.String(length=16), nullable=False),
    sa.Column('handle', sa.String(length=64), nullable=False),
    sa.Column('mode', sa.String(length=8), server_default='public', nullable=False),
    sa.Column('connected_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('scopes', JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['character_id'], ['characters.id'],
                            name=op.f('fk_social_channels_character_id_characters'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_social_channels')),
    sa.UniqueConstraint('platform', 'handle', name=op.f('uq_social_channels_platform'))
    )
    op.create_table('social_posts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('channel_id', sa.UUID(), nullable=False),
    sa.Column('platform_post_id', sa.String(length=64), nullable=False),
    sa.Column('canonical_id', sa.String(length=64), nullable=True),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('caption', sa.Text(), nullable=True),
    sa.Column('posted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('thumbnail_url', sa.Text(), nullable=True),
    sa.Column('duration_s', sa.Float(), nullable=True),
    sa.Column('inspired_by', sa.String(length=64), nullable=True),
    sa.Column('study', JSONB, nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['social_channels.id'],
                            name=op.f('fk_social_posts_channel_id_social_channels'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_social_posts')),
    sa.UniqueConstraint('channel_id', 'platform_post_id', name=op.f('uq_social_posts_channel_id'))
    )
    op.create_table('social_snapshots',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('post_id', sa.UUID(), nullable=False),
    sa.Column('taken_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('views', sa.BigInteger(), nullable=True),
    sa.Column('reach', sa.BigInteger(), nullable=True),
    sa.Column('likes', sa.BigInteger(), nullable=True),
    sa.Column('comments', sa.BigInteger(), nullable=True),
    sa.Column('shares', sa.BigInteger(), nullable=True),
    sa.Column('saves', sa.BigInteger(), nullable=True),
    sa.Column('avg_watch_s', sa.Float(), nullable=True),
    sa.Column('total_watch_s', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['post_id'], ['social_posts.id'],
                            name=op.f('fk_social_snapshots_post_id_social_posts'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_social_snapshots'))
    )
    op.create_index('ix_social_snapshots_post_taken', 'social_snapshots', ['post_id', 'taken_at'], unique=False)
    op.create_table('social_channel_snapshots',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('channel_id', sa.UUID(), nullable=False),
    sa.Column('taken_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('followers', sa.BigInteger(), nullable=True),
    sa.Column('total_likes', sa.BigInteger(), nullable=True),
    sa.Column('posts', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['channel_id'], ['social_channels.id'],
                            name=op.f('fk_social_channel_snapshots_channel_id_social_channels'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_social_channel_snapshots'))
    )
    op.create_index('ix_social_channel_snapshots_channel_taken', 'social_channel_snapshots',
                    ['channel_id', 'taken_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_social_channel_snapshots_channel_taken', table_name='social_channel_snapshots')
    op.drop_table('social_channel_snapshots')
    op.drop_index('ix_social_snapshots_post_taken', table_name='social_snapshots')
    op.drop_table('social_snapshots')
    op.drop_table('social_posts')
    op.drop_table('social_channels')
