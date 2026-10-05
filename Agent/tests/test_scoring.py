import random
from datetime import UTC, datetime, timedelta

import pytest

from tf_agent.scoring.directions import match_direction_key, normalize_key
from tf_agent.scoring.explore import DirectionStat, explore_ratio, split_tasks, thompson_rank
from tf_agent.scoring.subscores import freshness, momentum, overall, peer_stats, percentile
from tf_db.models import Video


def test_percentile_edges():
    assert percentile(5, [1, 2, 3, 4]) == 100.0
    assert percentile(0, [1, 2, 3, 4]) == 0.0
    assert percentile(2, [1, 2, 3, 4]) == pytest.approx(37.5)  # 1 below + half of 1 equal
    assert percentile(1, []) is None


def test_momentum_with_peers_and_fallback():
    peers_v = list(range(1, 101))
    peers_e = [i / 1000 for i in range(1, 101)]
    assert momentum(50.5, 0.0505, peers_v, peers_e) == pytest.approx(50.0, abs=1)
    assert momentum(None, 0.1, peers_v, peers_e) is None
    assert momentum(50.5, None, peers_v, peers_e) == pytest.approx(50.0, abs=1)
    high = momentum(100_000, 0.2, [], [])  # <20 peers → absolute log-scale fallback
    low = momentum(10, 0.01, [], [])
    assert high > 90 and low < 30


@pytest.mark.parametrize("age_h,expected", [(24, 100.0), (72, 100.0), (30 * 24, 20.0), (60 * 24, 20.0)])
def test_freshness_decay(age_h, expected):
    assert freshness(age_h) == pytest.approx(expected)


def test_freshness_saturation_and_unknown_age():
    assert freshness(24, saturation_pct=100) == pytest.approx(50.0)
    assert freshness(None) is None


def test_overall_weights_and_renormalization():
    sub = {"fit": 80, "feasibility": 70, "momentum": 50, "freshness": 100}
    assert overall(sub) == pytest.approx(0.4 * 80 + 0.3 * 70 + 0.2 * 50 + 0.1 * 100)
    no_momentum = {"fit": 80, "feasibility": 70, "momentum": None, "freshness": 100}
    assert overall(no_momentum) == pytest.approx((0.4 * 80 + 0.3 * 70 + 0.1 * 100) / 0.8)
    assert overall({"fit": None, "feasibility": 70}) is None


@pytest.mark.parametrize("s,expected", [(1, 0.85), (5, 0.55), (10, 0.175)])
def test_explore_ratio_table(s, expected):
    assert explore_ratio(s, rated_directions=5) == pytest.approx(expected)


def test_explore_ratio_first_runs_are_pure_exploration():
    assert explore_ratio(None, rated_directions=10) == 1.0
    assert explore_ratio(9, rated_directions=2) == 1.0


def test_split_tasks():
    assert split_tasks(10, 0.55) == (6, 4)
    assert split_tasks(10, 1.0) == (10, 0)
    assert split_tasks(3, 0.15) == (1, 2)  # always at least one new direction


def test_thompson_rank_is_deterministic_and_favours_winners():
    dirs = [DirectionStat("a", 20, 1), DirectionStat("b", 1, 20), DirectionStat("c", 0, 0)]
    first = [d.key for d, _ in thompson_rank(dirs, random.Random(42))]
    assert first == [d.key for d, _ in thompson_rank(dirs, random.Random(42))]
    wins = sum(thompson_rank(dirs, random.Random(i))[0][0].key == "a" for i in range(200))
    assert wins > 150


def test_direction_key_matching():
    assert normalize_key("Deadpan  Office/Chores!") == "deadpan-office-chores"
    existing = ["deadpan-office-chores", "gym-bro-seriousness"]
    assert match_direction_key("deadpan office chore", existing) == "deadpan-office-chores"
    assert match_direction_key("70s disco nostalgia", existing) is None


async def test_peer_stats_from_recent_videos(db_sessionmaker):
    now = datetime(2026, 10, 6, tzinfo=UTC)
    async with db_sessionmaker() as s:
        s.add(Video(canonical_id="tiktok:1", platform="tiktok", url="u", posted_at=now - timedelta(hours=10),
                    metrics={"views": 1000, "likes": 100}, metrics_at=now))
        s.add(Video(canonical_id="tiktok:2", platform="tiktok", url="u", posted_at=now - timedelta(days=90),
                    metrics={"views": 5}, metrics_at=now - timedelta(days=60)))
        s.add(Video(canonical_id="youtube:x", platform="youtube", url="u", posted_at=now, metrics={"views": 1},
                    metrics_at=now))
        await s.commit()
    vph, eng = await peer_stats(db_sessionmaker, "tiktok", now)
    assert vph == [pytest.approx(100.0)] and eng == [pytest.approx(0.1)]
