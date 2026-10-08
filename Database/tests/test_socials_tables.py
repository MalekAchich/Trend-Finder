"""Plan 9: our own channels, their posts and the numbers over time."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from tf_agent.testing import create_test_run
from tf_db.models import Character, SocialChannel, SocialChannelSnapshot, SocialPost, SocialSnapshot


async def test_channel_posts_and_snapshots(db_sessionmaker):
    char_id, _, _ = await create_test_run(db_sessionmaker, n_tasks=0)
    now = datetime.now(UTC)
    async with db_sessionmaker() as s:
        ch = SocialChannel(character_id=char_id, platform="tiktok", handle="made_up_channel")
        s.add(ch)
        await s.flush()
        post = SocialPost(channel_id=ch.id, platform_post_id="1000000000000000001",
                          url="https://www.tiktok.com/@made_up_channel/video/1000000000000000001")
        s.add(post)
        await s.flush()
        s.add_all([SocialSnapshot(post_id=post.id, taken_at=now, views=120, likes=9),
                   SocialChannelSnapshot(channel_id=ch.id, taken_at=now, followers=14)])
        await s.commit()
        assert ch.mode == "public" and ch.scopes == []
        s.add(SocialPost(channel_id=ch.id, platform_post_id="1000000000000000001", url="x"))
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()
        await s.execute(delete(Character).where(Character.id == char_id))
        await s.commit()
        assert (await s.execute(select(SocialSnapshot))).scalars().all() == []  # cascades from the character


async def test_one_channel_per_platform_and_handle(db_sessionmaker):
    char_id, _, _ = await create_test_run(db_sessionmaker, n_tasks=0)
    async with db_sessionmaker() as s:
        s.add(SocialChannel(character_id=char_id, platform="instagram", handle="made_up_channel"))
        await s.commit()
        s.add(SocialChannel(character_id=char_id, platform="instagram", handle="made_up_channel"))
        with pytest.raises(IntegrityError):
            await s.commit()
