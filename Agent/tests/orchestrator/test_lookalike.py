"""Lookalike discovery on made-up videos: seed creators, theme searches, second-degree creators, virality gate."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from tf_agent.discovery.lookalike import Gate, Lookalike, Pool, gate, theme_queries, velocity
from tf_agent.tools.types import Creator, Metrics, VideoItem

NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)


def vid(n, platform="instagram", handle="creator_a", plays=None, likes=None, days=2):
    url = {"instagram": f"https://www.instagram.com/reel/TESTREEL{n:03d}/",
           "tiktok": f"https://www.tiktok.com/@{handle}/video/{1000000000000000000 + n}",
           "youtube": f"https://www.youtube.com/shorts/TestShort{n:02d}"}[platform]
    cid = {"instagram": f"instagram:TESTREEL{n:03d}", "tiktok": f"tiktok:{1000000000000000000 + n}",
           "youtube": f"youtube:TestShort{n:02d}"}[platform]
    return VideoItem(canonical_id=cid, platform=platform, url=url, creator=Creator(handle=handle),
                     metrics=Metrics(views=plays, likes=likes), posted_at=NOW - timedelta(days=days))


def test_gate_keeps_recent_big_and_exploding_videos_fastest_first():
    pool = Pool()
    pool.add([vid(1, plays=2_000_000, days=1), vid(2, plays=2_000_000, days=10), vid(3, plays=50_000),
              vid(4, plays=900_000, days=60), vid(5, likes=40_000, days=2), vid(6, plays=5_000_000)], "test")
    picks, dropped = gate(pool, exclude={"instagram:TESTREEL006"}, g=Gate(days=31, min_plays=300_000), now=NOW)
    assert [p.item.canonical_id for p in picks] == ["instagram:TESTREEL001", "instagram:TESTREEL005",
                                                    "instagram:TESTREEL002"]  # likes x 25 counts when plays are hidden
    assert dropped == {"under 300,000 plays": 1, "older than 31 days": 1, "already yours or already found": 1}
    assert velocity(vid(7, plays=600_000, days=0), NOW) == 600_000 / 6  # very new videos aren't over-rewarded


def test_theme_queries_come_from_what_the_references_share_ai_first():
    studies = [{"niche": "AI celebrity edits", "trend_type": "dance", "tags": ["football", "celebrity", "viral"]},
               {"niche": "ai celebrity edits", "trend_type": "dance", "tags": ["football", "ai"]},
               {"niche": "sports motivation", "trend_type": "montage", "tags": ["basketball"]}]
    qs = theme_queries(studies, 8)
    assert qs[:2] == ["ai generated video viral", "ai influencer"]
    assert "ai celebrity edits" in qs and "ai sports motivation" in qs and "ai football" in qs
    assert "ai viral" not in qs and not any("ai ai" in q for q in qs) and len(qs) <= 8


def test_queries_are_plain_short_search_words():
    from tf_agent.discovery.lookalike import _query

    assert _query("AI character skit / POV vlog (AI)") == "ai character skit pov vlog"
    assert _query("Retro/historical character in modern life") == "ai retro historical character in"


def test_one_creator_cannot_fill_the_run_and_other_platforms_get_room():
    pool = Pool()
    pool.add([vid(n, handle="busy", plays=9_000_000, days=1) for n in range(1, 8)], "busy")
    pool.add([vid(50 + n, platform="tiktok", handle=f"tt{n}", plays=400_000, days=5) for n in range(4)], "tt")
    picks, _ = gate(pool, set(), Gate(max_judged=5, max_per_creator=3, max_platform_share=0.6), NOW)
    assert sum(p.item.creator.handle == "busy" for p in picks) == 3
    assert sum(p.item.platform == "tiktok" for p in picks) == 2


class FakeSources:
    def __init__(self):
        self.calls = []

    def _res(self, items):
        return SimpleNamespace(items=items)

    async def instagram_creator(self, handle, max_results=24):
        self.calls.append(("instagram_creator", handle))
        if handle == "creator_a":
            return self._res([vid(10, handle="creator_a", plays=3_000_000, days=3), vid(11, handle="creator_a", plays=1_000)])
        return self._res([vid(20, handle=handle, plays=8_000_000, days=1)])

    async def tiktok_creator(self, handle, max_results=15):
        self.calls.append(("tiktok_creator", handle))
        return self._res([])

    async def instagram_search(self, query, max_results=15, recent=None):
        self.calls.append(("instagram_search", query))
        return self._res([vid(30, handle="hit_maker", plays=4_000_000, days=2)] if query == "ai influencer" else [])

    async def tiktok_search(self, query, max_results=15, recent=None):
        self.calls.append(("tiktok_search", query))
        return self._res([])

    async def shorts_search(self, query, max_results=15, recent=None):
        self.calls.append(("shorts_search", query))
        return self._res([vid(40, platform="youtube", handle="yt_maker", plays=700_000, days=4)] if query == "ai influencer" else [])


async def test_discovery_follows_seeds_themes_and_the_creators_of_the_biggest_hits():
    events = []

    async def emit(type_, **payload):
        events.append((type_, payload))

    src = FakeSources()
    seed = vid(1, handle="creator_a", plays=2_000_000)
    picks, stats = await Lookalike(src, emit, Gate(max_queries=3), clock=lambda: NOW).discover(
        [seed], [{"niche": "ai stunts", "tags": ["dam"]}], exclude={seed.canonical_id})
    assert ("instagram_creator", "creator_a") in src.calls and ("instagram_creator", "hit_maker") in src.calls
    ids = [p.item.canonical_id for p in picks]
    assert ids == ["instagram:TESTREEL020", "instagram:TESTREEL030", "instagram:TESTREEL010", "youtube:TestShort40"]
    assert "creator of one of your references" in next(p.why for p in picks if p.item.canonical_id.endswith("010"))
    assert stats["second_degree"] == 1 and stats["dropped"] == {"under 300,000 plays": 1}
    kinds = [t for t, _ in events]
    assert kinds.count("agent.started") == kinds.count("agent.finished") == 5  # seeds, 3 platforms, hit creators
