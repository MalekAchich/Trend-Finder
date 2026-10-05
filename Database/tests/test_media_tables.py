import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tf_db.models import PlatformStateRow, ToolCacheRow, Video, VideoAnalysis


def tiktok(cid="tiktok:1"):
    return Video(canonical_id=cid, platform="tiktok", url=f"https://www.tiktok.com/@a/video/{cid[7:]}",
                 hashtags=["deadpan", "dance"], metrics={"views": 10})


async def test_video_roundtrip_with_hashtags(db_sessionmaker):
    async with db_sessionmaker() as s:
        s.add(tiktok())
        await s.commit()
        v = (await s.execute(select(Video))).scalar_one()
    assert v.hashtags == ["deadpan", "dance"] and v.metrics == {"views": 10}
    assert v.media_access == "unknown" and v.first_seen_at is not None


async def test_analysis_references_video_and_is_unique_per_version(db_sessionmaker):
    async with db_sessionmaker() as s:
        s.add(tiktok())
        await s.flush()
        s.add(VideoAnalysis(canonical_id="tiktok:1", pipeline_version="1", feasibility=84.5,
                            probe={"duration_s": 9.0}, cuts=[3.1]))
        await s.commit()
        a = (await s.execute(select(VideoAnalysis))).scalar_one()
        assert a.id.version == 7 and a.cuts == [3.1]
        s.add(VideoAnalysis(canonical_id="tiktok:1", pipeline_version="1"))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_analysis_requires_existing_video(db_sessionmaker):
    async with db_sessionmaker() as s:
        s.add(VideoAnalysis(canonical_id="tiktok:404", pipeline_version="1"))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_tool_cache_and_platform_state_defaults(db_sessionmaker):
    from datetime import UTC, datetime

    async with db_sessionmaker() as s:
        s.add(ToolCacheRow(key="k" * 64, tool="tiktok_search", response={"items": []},
                           expires_at=datetime(2030, 1, 1, tzinfo=UTC)))
        s.add(PlatformStateRow(platform="tiktok"))
        await s.commit()
        p = (await s.execute(select(PlatformStateRow))).scalar_one()
        c = (await s.execute(select(ToolCacheRow))).scalar_one()
    assert (p.health, p.breaker_state, p.failures) == ("ok", "closed", 0)
    assert c.response == {"items": []}
