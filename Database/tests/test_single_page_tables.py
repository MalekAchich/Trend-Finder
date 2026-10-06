"""Plan 5 Task 1: images-only character versions, owner targets, run inputs, thumbnails."""
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tf_agent.testing import create_test_run
from tf_db.models import CharacterVersion, Finding, Run, Target, Video, VideoAnalysis


async def test_targets_are_unique_per_character(db_sessionmaker):
    char_id, run_id, _ = await create_test_run(db_sessionmaker, n_tasks=0)
    async with db_sessionmaker() as s:
        s.add(Target(character_id=char_id, url="https://www.tiktok.com/@a/video/1", canonical_id="tiktok:1"))
        await s.commit()
        t = (await s.execute(select(Target))).scalar_one()
        assert t.status == "pending" and t.run_id is None
        s.add(Target(character_id=char_id, url="https://www.tiktok.com/@b/video/1", canonical_id="tiktok:1"))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_new_columns_have_defaults(db_sessionmaker):
    char_id, run_id, _ = await create_test_run(db_sessionmaker, n_tasks=0)
    async with db_sessionmaker() as s:
        run = await s.get(Run, run_id)
        assert run.inputs == {} and run.feedback_contributions == {} and run.character_read is None
        v = (await s.execute(select(CharacterVersion))).scalars().first()
        assert isinstance(v.images, list)
        s.add(Video(canonical_id="tiktok:9", platform="tiktok", url="https://www.tiktok.com/@a/video/9"))
        await s.flush()
        s.add(VideoAnalysis(canonical_id="tiktok:9", pipeline_version="1", thumbnail_path="/m/thumbs/t.jpg"))
        s.add(Finding(run_id=run_id, canonical_id="tiktok:9"))
        await s.commit()
        f = (await s.execute(select(Finding))).scalar_one()
        assert f.source == "agent"
