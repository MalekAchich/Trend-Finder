"""providers: models, provider_state, model_calls

Revision ID: 0001
Revises:
Create Date: 2026-10-05
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "models",
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(256), nullable=True),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("available", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("provider", "model_id", name=op.f("pk_models")),
    )
    op.create_table(
        "provider_state",
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), server_default="ok", nullable=False),
        sa.Column("cooling_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_percent", sa.Float(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("provider", name=op.f("pk_provider_state")),
    )
    op.create_table(
        "model_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("images", sa.Integer(), server_default="0", nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_class", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_calls")),
    )
    op.create_index(op.f("ix_model_calls_run_id"), "model_calls", ["run_id"])
    op.create_index(op.f("ix_model_calls_created_at"), "model_calls", ["created_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_model_calls_created_at"), table_name="model_calls")
    op.drop_index(op.f("ix_model_calls_run_id"), table_name="model_calls")
    op.drop_table("model_calls")
    op.drop_table("provider_state")
    op.drop_table("models")
