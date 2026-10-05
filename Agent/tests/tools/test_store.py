from datetime import UTC, datetime

from sqlalchemy import select

from tf_agent.tools.store import VideoStore
from tf_agent.tools.types import Creator, Metrics, Sound, VideoItem
from tf_db.models import Video


def item(views):
    return VideoItem(canonical_id="tiktok:1", platform="tiktok", url="https://www.tiktok.com/@a/video/1",
                     creator=Creator(handle="a", followers=5), caption="c #deadpan", hashtags=["deadpan"],
                     sound=Sound(id="s1", title="song"), posted_at=datetime(2026, 9, 1, tzinfo=UTC),
                     duration_s=9.0, metrics=Metrics(views=views, likes=3), media_access="ok")


async def test_upsert_video_updates_metrics(db_sessionmaker):
    store = VideoStore(db_sessionmaker)
    await store.upsert_videos([item(10)])
    await store.upsert_videos([item(99)])
    async with db_sessionmaker() as s:
        row = (await s.execute(select(Video))).scalar_one()
    assert row.metrics["views"] == 99 and row.sound_id == "s1" and row.hashtags == ["deadpan"]
    assert row.creator_followers == 5 and row.metrics_at is not None


async def test_snippet_only_item_never_erases_known_metrics(db_sessionmaker):
    store = VideoStore(db_sessionmaker)
    await store.upsert_videos([item(10)])
    await store.upsert_videos([VideoItem(canonical_id="tiktok:1", platform="tiktok", url="u", source="searxng")])
    got = await store.get_video("tiktok:1")
    assert got.metrics.views == 10 and got.caption == "c #deadpan"
