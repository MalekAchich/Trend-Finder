"""media & tools: videos, video_analyses, tool_cache, platform_state

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "videos",
        sa.Column("canonical_id", sa.String(64), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("creator_handle", sa.String(128), nullable=True),
        sa.Column("creator_followers", sa.BigInteger(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("hashtags", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
        sa.Column("sound_id", sa.String(128), nullable=True),
        sa.Column("sound_title", sa.Text(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"),
                  nullable=False),
        sa.Column("metrics_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("media_access", sa.String(16), server_default="unknown", nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("canonical_id", name=op.f("pk_videos")),
    )
    op.create_index(op.f("ix_videos_sound_id"), "videos", ["sound_id"])
    op.create_table(
        "video_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_id", sa.String(64), nullable=False),
        sa.Column("pipeline_version", sa.String(16), nullable=False),
        sa.Column("probe", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("cuts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("cut_rate", sa.Float(), nullable=True),
        sa.Column("transcript", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("pose", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("camera_motion", sa.Float(), nullable=True),
        sa.Column("best_clean_segment", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("feasibility", sa.Float(), nullable=True),
        sa.Column("filtered_reason", sa.String(64), nullable=True),
        sa.Column("fingerprint", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("contact_sheet_path", sa.Text(), nullable=True),
        sa.Column("media_path", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["canonical_id"], ["videos.canonical_id"], ondelete="CASCADE",
                                name=op.f("fk_video_analyses_canonical_id_videos")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_video_analyses")),
        sa.UniqueConstraint("canonical_id", "pipeline_version", name=op.f("uq_video_analyses_canonical_id")),
    )
    op.create_index(op.f("ix_video_analyses_canonical_id"), "video_analyses", ["canonical_id"])
    op.create_table(
        "tool_cache",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_tool_cache")),
    )
    op.create_index(op.f("ix_tool_cache_expires_at"), "tool_cache", ["expires_at"])
    op.create_table(
        "platform_state",
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("health", sa.String(16), server_default="ok", nullable=False),
        sa.Column("breaker_state", sa.String(16), server_default="closed", nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("quota_used_today", sa.Integer(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("platform", name=op.f("pk_platform_state")),
    )


def downgrade() -> None:
    op.drop_table("platform_state")
    op.drop_index(op.f("ix_tool_cache_expires_at"), table_name="tool_cache")
    op.drop_table("tool_cache")
    op.drop_index(op.f("ix_video_analyses_canonical_id"), table_name="video_analyses")
    op.drop_table("video_analyses")
    op.drop_index(op.f("ix_videos_sound_id"), table_name="videos")
    op.drop_table("videos")
