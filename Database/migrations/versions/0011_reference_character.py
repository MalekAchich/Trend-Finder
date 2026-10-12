"""a reference belongs to one character (D-60): the existing ones go to the character that studied them

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-12 23:30:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('manual_videos', sa.Column('reference_character_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key('manual_videos_reference_character_id_fkey', 'manual_videos', 'characters',
                          ['reference_character_id'], ['id'], ondelete='SET NULL')
    # a reference studied for exactly one character was that character's; the rest stay every character's
    op.execute("""
        UPDATE manual_videos m SET reference_character_id = s.character_id
        FROM (SELECT manual_video_id, min(character_id::text)::uuid AS character_id FROM reference_studies
              GROUP BY manual_video_id HAVING count(DISTINCT character_id) = 1) s
        WHERE m.id = s.manual_video_id AND m.is_reference
    """)


def downgrade() -> None:
    op.drop_constraint('manual_videos_reference_character_id_fkey', 'manual_videos', type_='foreignkey')
    op.drop_column('manual_videos', 'reference_character_id')
