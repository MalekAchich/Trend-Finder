"""single page: images-only characters, owner targets, run inputs, thumbnails; drop briefs

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-06 18:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column('character_versions', sa.Column('images', JSONB, server_default=sa.text("'[]'::jsonb"),
                                                  nullable=False))
    op.execute("update character_versions set images = jsonb_build_array(canonical_image_path)")
    op.drop_column('character_versions', 'profile_md')
    op.drop_column('character_versions', 'front_matter')

    op.drop_table('seeds')
    op.create_table('targets',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('character_id', sa.UUID(), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('canonical_id', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='pending', nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['character_id'], ['characters.id'], name=op.f('fk_targets_character_id_characters'),
                            ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], name=op.f('fk_targets_run_id_runs'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_targets')),
    sa.UniqueConstraint('character_id', 'canonical_id', name=op.f('uq_targets_character_id'))
    )

    op.add_column('video_analyses', sa.Column('thumbnail_path', sa.Text(), nullable=True))
    op.add_column('runs', sa.Column('inputs', JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.add_column('runs', sa.Column('character_read', JSONB, nullable=True))
    op.add_column('runs', sa.Column('feedback_contributions', JSONB, server_default=sa.text("'{}'::jsonb"),
                                    nullable=False))
    op.execute("update runs r set feedback_contributions = f.contributions from run_feedback f where f.run_id = r.id")
    op.drop_column('run_feedback', 'contributions')
    op.add_column('findings', sa.Column('source', sa.String(length=8), server_default='agent', nullable=False))

    op.drop_table('briefs')
    op.execute("delete from settings where key in ('weights', 'weight_suggestion')")
    op.execute("delete from card_feedback where rating = 'skip'")
    op.drop_constraint(op.f('ck_card_feedback_rating'), 'card_feedback', type_='check')
    op.create_check_constraint(op.f('ck_card_feedback_rating'), 'card_feedback', "rating in ('up', 'down')")


def downgrade() -> None:
    op.drop_constraint(op.f('ck_card_feedback_rating'), 'card_feedback', type_='check')
    op.create_check_constraint(op.f('ck_card_feedback_rating'), 'card_feedback', "rating in ('up', 'down', 'skip')")
    op.create_table('briefs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('cluster_id', sa.UUID(), nullable=False),
    sa.Column('character_version_id', sa.UUID(), nullable=True),
    sa.Column('body', JSONB, nullable=False),
    sa.Column('body_md', sa.Text(), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=True),
    sa.Column('model', sa.String(length=128), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['character_version_id'], ['character_versions.id'],
                            name=op.f('fk_briefs_character_version_id_character_versions')),
    sa.ForeignKeyConstraint(['cluster_id'], ['trend_clusters.id'], name=op.f('fk_briefs_cluster_id_trend_clusters'),
                            ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_briefs')),
    sa.UniqueConstraint('cluster_id', name=op.f('uq_briefs_cluster_id'))
    )
    op.drop_column('findings', 'source')
    op.add_column('run_feedback', sa.Column('contributions', JSONB, server_default='{}', nullable=False))
    op.drop_column('runs', 'feedback_contributions')
    op.drop_column('runs', 'character_read')
    op.drop_column('runs', 'inputs')
    op.drop_column('video_analyses', 'thumbnail_path')
    op.drop_table('targets')
    op.create_table('seeds',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('character_id', sa.UUID(), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('canonical_id', sa.String(length=64), nullable=True),
    sa.Column('source', sa.String(length=16), server_default='profile', nullable=False),
    sa.Column('study', JSONB, nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['character_id'], ['characters.id'], name=op.f('fk_seeds_character_id_characters'),
                            ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_seeds')),
    sa.UniqueConstraint('character_id', 'url', name=op.f('uq_seeds_character_id'))
    )
    op.add_column('character_versions', sa.Column('front_matter', JSONB, server_default=sa.text("'{}'::jsonb"),
                                                  nullable=False))
    op.add_column('character_versions', sa.Column('profile_md', sa.Text(), server_default='', nullable=False))
    op.drop_column('character_versions', 'images')
