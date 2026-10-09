"""Plan 9: the channel report (facts for the judges and the learner), on made-up channels and numbers."""
from datetime import UTC, datetime, timedelta

from tf_agent.socials.report import channel_report
from tf_agent.testing import create_test_run
from tf_db.models import SocialChannel, SocialChannelSnapshot, SocialPost, SocialSnapshot, Video

NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)


async def seed(sm):
    char_id, _, _ = await create_test_run(sm, n_tasks=0)
    async with sm() as s:
        s.add(Video(canonical_id="instagram:TESTREEL050", platform="instagram",
                    url="https://www.instagram.com/reel/TESTREEL050/", creator_handle="made_up_source"))
        ig = SocialChannel(character_id=char_id, platform="instagram", handle="made_up_channel", mode="api",
                           audience={"reached": {"country": [["US", 60], ["TN", 40]], "age": [["18-24", 10]]}})
        s.add(ig)
        await s.flush()
        s.add_all([SocialChannelSnapshot(channel_id=ig.id, taken_at=NOW - timedelta(days=8), followers=100, posts=1),
                   SocialChannelSnapshot(channel_id=ig.id, taken_at=NOW, followers=140, posts=2)])
        slow = SocialPost(channel_id=ig.id, platform_post_id="TESTOWN001", url="u1", caption="slow made-up post",
                          posted_at=NOW - timedelta(days=5), duration_s=12)
        fast = SocialPost(channel_id=ig.id, platform_post_id="TESTOWN002", url="u2", posted_at=NOW - timedelta(hours=20),
                          duration_s=10, inspired_by="instagram:TESTREEL050",
                          study={"format": "gas-station dance in a 70s suit", "tags": ["dance", "ai"]})
        old = SocialPost(channel_id=ig.id, platform_post_id="TESTOWN000", url="u0", posted_at=NOW - timedelta(days=60))
        s.add_all([slow, fast, old])
        await s.flush()
        s.add_all([SocialSnapshot(post_id=slow.id, taken_at=NOW, views=900, likes=30),
                   SocialSnapshot(post_id=fast.id, taken_at=NOW, views=2000, likes=150, comments=20, shares=20,
                                  saves=10, reach=1500, avg_watch_s=6.0),
                   SocialSnapshot(post_id=old.id, taken_at=NOW, views=99_999)])
        await s.commit()
    return char_id


async def test_the_report_ranks_recent_posts_by_views_per_hour_with_what_they_are(db_sessionmaker):
    char_id = await seed(db_sessionmaker)
    text = await channel_report(db_sessionmaker, char_id, NOW)
    lines = text.splitlines()
    assert lines[0].startswith("Your own channels (facts") and "not verdicts" in lines[0]
    assert lines[1] == "- Instagram @made_up_channel: 140 followers (+40 in 7 days), 2 posts."
    assert lines[2] == "  Reached (who saw the videos): countries US 60%, TN 40% · ages 18-24 100%"
    best, second = lines[4], lines[5]
    assert best.startswith("- Instagram: 2.0k views in 20 h (100/h), 10.0% engagement, reach 1.5k, avg watch 6.0 s of 10 s (60%)")
    assert "gas-station dance in a 70s suit" in best and "tags: dance, ai" in best
    assert "recreated from @made_up_source's video" in best
    assert "slow made-up post" in second and "engagement" in second and "99" not in text.split("best first")[1].split("slow")[0]
    assert len([ln for ln in lines if ln.startswith("- Instagram:")]) == 2  # the 60-day-old post isn't recent


async def test_no_channels_means_no_report_and_unknown_numbers_show_as_a_dash(db_sessionmaker):
    char_id, _, _ = await create_test_run(db_sessionmaker, n_tasks=0)
    assert await channel_report(db_sessionmaker, char_id, NOW) is None
    async with db_sessionmaker() as s:
        tt = SocialChannel(character_id=char_id, platform="tiktok", handle="made_up_channel")
        s.add(tt)
        await s.flush()
        p = SocialPost(channel_id=tt.id, platform_post_id="1000000000000000010", url="u", posted_at=NOW - timedelta(hours=3))
        s.add(p)
        await s.flush()
        s.add(SocialSnapshot(post_id=p.id, taken_at=NOW, views=None, likes=4))
        await s.commit()
    text = await channel_report(db_sessionmaker, char_id, NOW)
    assert "TikTok @made_up_channel: – followers, – posts." in text and "– views" in text and "engagement –" in text
