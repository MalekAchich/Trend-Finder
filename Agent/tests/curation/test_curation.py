from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select

from tf_agent.curation.cluster import Member, best_source, cluster_members
from tf_agent.curation.curate import Curator
from tf_agent.models.fake import FakeAdapter, text_response
from tf_agent.pipeline import PIPELINE_VERSION
from tf_agent.roles.runners import Roles
from tf_agent.testing import create_test_run, make_client
from tf_db.models import Finding, FindingScore, TrendCluster, TrendMember, Video, VideoAnalysis

A = ["0000000000000000"] * 12
A_NEAR = ["0000000000000001"] * 12
B = ["ffffffffffffffff"] * 12


def m(fid, sound=None, hashes=A, feas=70.0, height=1280, momentum=50.0, overall=60.0):
    return Member(finding_id=fid, canonical_id=f"tiktok:{fid}", sound_id=sound, frame_hashes=hashes,
                  feasibility=feas, height=height, momentum=momentum, overall=overall)


def test_reposts_cluster_and_distinct_videos_do_not():
    groups = cluster_members([m("1", "s1", A), m("2", "s1", A_NEAR), m("3", "s2", B), m("4", None, A_NEAR)])
    assert sorted(sorted(x.finding_id for x in g) for g in groups) == [["1", "2", "4"], ["3"]]


def test_same_sound_alone_is_not_enough():
    assert len(cluster_members([m("1", "s1", A), m("2", "s1", B)])) == 2


def test_best_source_prefers_feasibility_then_resolution():
    g = [m("1", feas=70, height=720), m("2", feas=85, height=720), m("3", feas=85, height=1280)]
    assert best_source(g).finding_id == "3"


async def add_finding(sm, run_id, task_id, cid, hashes, fit, overall, sound=None, feas=80.0, provider="a",
                      sheet="/tmp/s.jpg"):
    now = datetime.now(UTC)
    async with sm() as s:
        s.add(Video(canonical_id=cid, platform="tiktok", url=f"https://www.tiktok.com/@u/video/{cid[7:]}",
                    sound_id=sound, posted_at=now - timedelta(hours=10), metrics={"views": 1000}, metrics_at=now))
        await s.flush()
        s.add(VideoAnalysis(canonical_id=cid, pipeline_version=PIPELINE_VERSION, feasibility=feas, probe={"height": 1280},
                            fingerprint={"frame_hashes": hashes}, contact_sheet_path=sheet))
        f = Finding(run_id=run_id, task_id=task_id, canonical_id=cid, status="analyzed")
        s.add(f)
        await s.flush()
        s.add(FindingScore(finding_id=f.id, fit=fit, feasibility=feas, momentum=50.0, freshness=100.0,
                           overall=overall, analyst_provider=provider, niche_guess="deadpan professional"))
        await s.commit()
        return f.id


async def test_curate_clusters_cross_checks_and_ranks(db_sessionmaker, tmp_path):
    sheet = tmp_path / "s.jpg"
    sheet.write_bytes(b"x")
    canon = tmp_path / "c.png"
    canon.write_bytes(b"x")
    _, run_id, (task_id,) = await create_test_run(db_sessionmaker)
    await add_finding(db_sessionmaker, run_id, task_id, "tiktok:1", A, 80, 75, sound="s1", sheet=str(sheet))
    await add_finding(db_sessionmaker, run_id, task_id, "tiktok:2", A_NEAR, 70, 65, sound="s1", feas=90,
                      sheet=str(sheet))
    await add_finding(db_sessionmaker, run_id, task_id, "tiktok:3", B, 60, 55, sheet=str(sheet))
    a = FakeAdapter("a")
    b = FakeAdapter("b", [text_response("b", structured={"fit": 20, "justification": "not his vibe"}),
                          text_response("b", structured={"fit": 62, "justification": "acceptable fit"})])
    client, _, _ = make_client({"a": a, "b": b})
    character = SimpleNamespace(brief="# Character: N", canonical_image_path=str(canon))
    await Curator(db_sessionmaker, Roles(client), top_k=5).curate(run_id, character)
    async with db_sessionmaker() as s:
        clusters = (await s.execute(select(TrendCluster).where(TrendCluster.run_id == run_id)
                                    .order_by(TrendCluster.rank))).scalars().all()
        members = (await s.execute(select(TrendMember))).scalars().all()
        scores = {f.canonical_id: sc for f, sc in (await s.execute(select(Finding, FindingScore).join(
            FindingScore, FindingScore.finding_id == Finding.id))).all()}
    assert [c.member_count for c in clusters] == [2, 1] or [c.member_count for c in clusters] == [1, 2]
    assert len(members) == 3 and [c.rank for c in clusters] == [1, 2]
    best = scores["tiktok:2"]  # highest feasibility in the repost cluster becomes its best source
    assert any(c.best_finding_id is not None for c in clusters)
    assert best.cross_provider == "b" and best.disagreement is True  # 70 vs 20
    assert best.fit == 70 and best.cross_fit == 20  # the analyst's fit is kept; the score uses their mean
    assert len(b.requests) == 2 and a.requests == []
    assert clusters[0].overall >= clusters[1].overall


async def test_curation_is_idempotent(db_sessionmaker, tmp_path):
    sheet = tmp_path / "s.jpg"
    sheet.write_bytes(b"x")
    _, run_id, (task_id,) = await create_test_run(db_sessionmaker)
    await add_finding(db_sessionmaker, run_id, task_id, "tiktok:1", A, 80, 75, sheet=str(sheet))
    client, _, _ = make_client({"a": FakeAdapter("a"), "b": FakeAdapter("b", [
        text_response("b", structured={"fit": 75, "justification": "agree"})] * 2)})
    curator = Curator(db_sessionmaker, Roles(client), top_k=5)
    ch = SimpleNamespace(brief="b", canonical_image_path=str(sheet))
    await curator.curate(run_id, ch)
    await curator.curate(run_id, ch)
    async with db_sessionmaker() as s:
        assert len((await s.execute(select(TrendCluster))).scalars().all()) == 1


async def test_second_curation_keeps_analyst_fit_and_skips_cross_check(db_sessionmaker, tmp_path):
    sheet = tmp_path / "s.jpg"
    sheet.write_bytes(b"x")
    _, run_id, (task_id,) = await create_test_run(db_sessionmaker)
    await add_finding(db_sessionmaker, run_id, task_id, "tiktok:1", A, 80, 75, sheet=str(sheet))
    b = FakeAdapter("b", [text_response("b", structured={"fit": 20, "justification": "not his vibe"})])
    client, _, _ = make_client({"a": FakeAdapter("a"), "b": b})
    curator = Curator(db_sessionmaker, Roles(client), top_k=5)
    ch = SimpleNamespace(brief="b", canonical_image_path=str(sheet))
    await curator.curate(run_id, ch)
    await curator.curate(run_id, ch)
    async with db_sessionmaker() as s:
        sc = (await s.execute(select(FindingScore))).scalar_one()
    assert sc.fit == 80 and sc.cross_fit == 20 and len(b.requests) == 1


async def test_curation_never_destroys_owner_feedback(db_sessionmaker, tmp_path):
    from tf_db.models import CardFeedback

    sheet = tmp_path / "s.jpg"
    sheet.write_bytes(b"x")
    _, run_id, (task_id,) = await create_test_run(db_sessionmaker)
    await add_finding(db_sessionmaker, run_id, task_id, "tiktok:1", A, 80, 75, sheet=str(sheet))
    client, _, _ = make_client({"a": FakeAdapter("a"), "b": FakeAdapter("b", [
        text_response("b", structured={"fit": 75, "justification": "agree"})])})
    curator = Curator(db_sessionmaker, Roles(client), top_k=5)
    ch = SimpleNamespace(brief="b", canonical_image_path=str(sheet))
    await curator.curate(run_id, ch)
    async with db_sessionmaker() as s:
        cluster = (await s.execute(select(TrendCluster))).scalar_one()
        s.add(CardFeedback(cluster_id=cluster.id, rating="up"))
        await s.commit()
        cid = cluster.id
    await curator.curate(run_id, ch)
    async with db_sessionmaker() as s:
        assert (await s.execute(select(TrendCluster))).scalar_one().id == cid
        assert len((await s.execute(select(CardFeedback))).scalars().all()) == 1


async def test_the_owners_bar_applies_after_the_second_opinion(db_sessionmaker, tmp_path):
    sheet = tmp_path / "s.jpg"
    sheet.write_bytes(b"x")
    _, run_id, (task_id,) = await create_test_run(db_sessionmaker)
    await add_finding(db_sessionmaker, run_id, task_id, "tiktok:1", A, 90, 85, sheet=str(sheet))
    await add_finding(db_sessionmaker, run_id, task_id, "tiktok:2", B, 82, 78, sheet=str(sheet))
    b = FakeAdapter("b", [text_response("b", structured={"fit": 88, "justification": "agree"}),
                          text_response("b", structured={"fit": 30, "justification": "not like the references"})])
    client, _, _ = make_client({"a": FakeAdapter("a"), "b": b})
    ch = SimpleNamespace(brief="b", canonical_image_path=str(sheet))
    await Curator(db_sessionmaker, Roles(client), top_k=5).curate(run_id, ch, min_score=75)
    async with db_sessionmaker() as s:
        kept = (await s.execute(select(Finding.canonical_id).join(TrendCluster, TrendCluster.best_finding_id == Finding.id)
                                )).scalars().all()
        status = dict((await s.execute(select(Finding.canonical_id, Finding.status))).all())
    assert kept == ["tiktok:1"] and status["tiktok:2"] == "below_bar"
