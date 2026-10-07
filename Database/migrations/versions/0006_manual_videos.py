"""manually chosen videos (references and targets), reference studies, richer analyst fields

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-07 17:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table('manual_videos',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('canonical_id', sa.String(length=64), nullable=True),
    sa.Column('platform', sa.String(length=16), nullable=False),
    sa.Column('is_reference', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('target_character_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='checking', nullable=False),
    sa.Column('problem', sa.Text(), nullable=True),
    sa.Column('thumbnail_path', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['target_character_id'], ['characters.id'],
                            name=op.f('fk_manual_videos_target_character_id_characters'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_manual_videos')),
    sa.UniqueConstraint('canonical_id', name=op.f('uq_manual_videos_canonical_id'))
    )
    op.create_table('reference_studies',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('manual_video_id', sa.UUID(), nullable=False),
    sa.Column('character_id', sa.UUID(), nullable=False),
    sa.Column('study', JSONB, nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['character_id'], ['characters.id'],
                            name=op.f('fk_reference_studies_character_id_characters'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['manual_video_id'], ['manual_videos.id'],
                            name=op.f('fk_reference_studies_manual_video_id_manual_videos'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_reference_studies')),
    sa.UniqueConstraint('manual_video_id', 'character_id', name=op.f('uq_reference_studies_manual_video_id'))
    )
    op.add_column('finding_scores', sa.Column('tags', JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.add_column('finding_scores', sa.Column('trend_type', sa.String(length=64), nullable=True))
    op.add_column('finding_scores', sa.Column('audio_use', sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column('finding_scores', 'audio_use')
    op.drop_column('finding_scores', 'trend_type')
    op.drop_column('finding_scores', 'tags')
    op.drop_table('reference_studies')
    op.drop_table('manual_videos')
