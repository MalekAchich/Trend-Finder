import dataclasses
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from tf_agent.models.errors import UsageLimited
from tf_agent.models.fake import FakeAdapter, text_response
from tf_agent.orchestrator.analysis import AnalysisStage, CandidateSink
from tf_agent.orchestrator.queue import Requeue, TaskQueue
from tf_agent.pipeline.result import VideoAnalysisResult
from tf_agent.roles.runners import Roles
from tf_agent.roles.schemas import Candidate
from tf_agent.testing import make_client
from tf_agent.tools.store import VideoStore
from tf_agent.tools.types import Metrics, VideoItem
from tf_db.models import Finding, FindingScore

from .test_blackboard import make_run

ANALYSIS = {"fit_breakdown": {"look": 8, "vibe": 9, "energy": 7, "niche": 6, "adaptability": 8},
            "justification": "stiff dance suits him", "adaptation_idea": "bank lobby",
            "feasibility_notes": "single person", "niche_guess": "deadpan professional"}
NOW = datetime.now(UTC)


class FakeAnalyzer:
    def __init__(self, result):
        self.result, self.calls = result, 0

    async def analyze(self, item):
        self.calls += 1
        return self.result


async def setup(sm, tmp_path, analysis, script):
    char_id, run, (task_id,) = await make_run(sm, n_tasks=1)
    store = VideoStore(sm)
    await store.upsert_videos([VideoItem(canonical_id="tiktok:1", platform="tiktok",
                                         url="https://www.tiktok.com/@u/video/1", posted_at=NOW - timedelta(hours=5),
                                         duration_s=9, metrics=Metrics(views=50_000, likes=5000))])
    canon = tmp_path / "canon.png"
    canon.write_bytes(b"x")
    character = SimpleNamespace(character_id=char_id, brief="# Character: N", canonical_image_path=str(canon))
    fa = FakeAdapter("a", script)
    client, _, _ = make_client({"a": fa})
    q = TaskQueue(sm)
    stage = AnalysisStage(sm, FakeAnalyzer(analysis), Roles(client), store, character)
    return run, task_id, q, stage, fa


def ok_analysis(tmp_path):
    sheet = tmp_path / "sheet.jpg"
    sheet.write_bytes(b"x")
    return VideoAnalysisResult(canonical_id="tiktok:1", feasibility=80.0, contact_sheet_path=str(sheet),
                               pose={"single_person_ratio": 1.0}, transcript={"text": ""})


async def test_sink_accepts_real_rejects_unknown_and_duplicates(db_sessionmaker, tmp_path):
    run, task_id, q, stage, _ = await setup(db_sessionmaker, tmp_path, ok_analysis(tmp_path), [])
    sink = CandidateSink(db_sessionmaker, q)
    cands = [Candidate(canonical_id="tiktok:1", why="good", preliminary_fit=7),
             Candidate(canonical_id="tiktok:999", why="made up", preliminary_fit=9),
             Candidate(canonical_id="not an id", why="junk", preliminary_fit=1)]
    accepted, rejected = await sink.submit(run, task_id, None, cands)
    assert accepted == ["tiktok:1"]
    assert {c for c, _ in rejected} == {"tiktok:999", "not an id"}
    again, rejected2 = await sink.submit(run, task_id, None, cands[:1])
    assert again == [] and rejected2 == [("tiktok:1", "already submitted in this run")]
    assert await q.outstanding(run, ["analyze"]) == 1


async def run_one(db_sessionmaker, q, stage, run, task_id):
    await CandidateSink(db_sessionmaker, q).submit(run, task_id, None,
                                                   [Candidate(canonical_id="tiktok:1", why="deadpan fit", preliminary_fit=7)])
    task = await q.claim_next(run, ["analyze"])
    return await stage.handle(task)


async def test_analysis_writes_scores(db_sessionmaker, tmp_path):
    run, task_id, q, stage, fa = await setup(db_sessionmaker, tmp_path, ok_analysis(tmp_path),
                                             [text_response("a", structured=ANALYSIS)])
    out = await run_one(db_sessionmaker, q, stage, run, task_id)
    async with db_sessionmaker() as s:
        f = (await s.execute(select(Finding))).scalar_one()
        sc = (await s.execute(select(FindingScore))).scalar_one()
    assert f.status == "analyzed" and out["overall"] == sc.overall
    assert sc.fit == pytest.approx(72.0) and sc.feasibility == 80.0 and sc.freshness == 100.0
    assert sc.momentum is not None and sc.overall is not None and sc.analyst_provider == "a"
    assert [i.path for i in fa.requests[0].messages[0].images()][0].endswith("canon.png")


async def test_filtered_video_skips_the_analyst(db_sessionmaker, tmp_path):
    filtered = VideoAnalysisResult.filtered("tiktok:1", "too_long")
    run, task_id, q, stage, fa = await setup(db_sessionmaker, tmp_path, filtered, [])
    out = await run_one(db_sessionmaker, q, stage, run, task_id)
    async with db_sessionmaker() as s:
        f = (await s.execute(select(Finding))).scalar_one()
    assert f.status == "filtered_feasibility" and out == {"filtered": "too_long"} and fa.requests == []


async def test_several_people_or_a_close_up_cost_score_but_the_analyst_still_judges_it(db_sessionmaker, tmp_path):
    flagged = dataclasses.replace(ok_analysis(tmp_path), filtered_reason="multiple_people", feasibility=45.0)
    run, task_id, q, stage, fa = await setup(db_sessionmaker, tmp_path, flagged,
                                             [text_response("a", structured=ANALYSIS)])
    out = await run_one(db_sessionmaker, q, stage, run, task_id)
    async with db_sessionmaker() as s:
        f = (await s.execute(select(Finding))).scalar_one()
        sc = (await s.execute(select(FindingScore))).scalar_one()
    assert "overall" in out and f.status == "analyzed" and len(fa.requests) == 1
    assert sc.feasibility == 45.0 and sc.feasibility_notes.startswith("Measured: multiple people.")


async def test_invalid_analyst_output_fails_the_finding_not_the_run(db_sessionmaker, tmp_path):
    bad = text_response("a", structured={"nope": 1})
    run, task_id, q, stage, _ = await setup(db_sessionmaker, tmp_path, ok_analysis(tmp_path), [bad, bad])
    out = await run_one(db_sessionmaker, q, stage, run, task_id)
    async with db_sessionmaker() as s:
        assert (await s.execute(select(Finding))).scalar_one().status == "failed"
    assert "invalid" in out["error"]


async def test_usage_limits_requeue_the_analysis(db_sessionmaker, tmp_path):
    reset = (NOW + timedelta(minutes=30)).timestamp()
    run, task_id, q, stage, _ = await setup(db_sessionmaker, tmp_path, ok_analysis(tmp_path),
                                            [UsageLimited("a", "limit", reset_at=reset)])
    with pytest.raises(Requeue) as ei:
        await run_one(db_sessionmaker, q, stage, run, task_id)
    assert abs(ei.value.not_before.timestamp() - reset) < 1


# ---- Plan 7: one scoring model per run, so scores in a run are comparable ----
async def two_provider_stage(sm, tmp_path, script_a, script_b):
    char_id, run, (task_id,) = await make_run(sm, n_tasks=1)
    store = VideoStore(sm)
    await store.upsert_videos([VideoItem(canonical_id=f"tiktok:{i}", platform="tiktok",
                                         url=f"https://www.tiktok.com/@u/video/{i}", posted_at=NOW - timedelta(hours=5),
                                         duration_s=9, metrics=Metrics(views=50_000, likes=5000)) for i in (1, 2)])
    canon = tmp_path / "canon.png"
    canon.write_bytes(b"x")
    character = SimpleNamespace(character_id=char_id, brief="# Character: N", canonical_image_path=str(canon))
    fa, fb = FakeAdapter("a", script_a), FakeAdapter("b", script_b)
    client, _, _ = make_client({"a": fa, "b": fb})
    events = []

    async def emit(run_id, type_, payload):
        events.append((type_, payload))

    stage = AnalysisStage(sm, FakeAnalyzer(ok_analysis(tmp_path)), Roles(client), store, character, emit=emit)
    q = TaskQueue(sm)
    await CandidateSink(sm, q).submit(run, task_id, None, [Candidate(canonical_id=f"tiktok:{i}", why="fit",
                                                                     preliminary_fit=7) for i in (1, 2)])
    return run, q, stage, fa, fb, events, client


async def test_the_run_keeps_the_scoring_model_of_its_first_analysis(db_sessionmaker, tmp_path):
    from tf_agent.models.errors import TransientProviderError

    run, q, stage, fa, fb, _, client = await two_provider_stage(
        db_sessionmaker, tmp_path, [], [text_response("b", structured=ANALYSIS)] * 2)
    fa.push(*[TransientProviderError("a", "blip")] * client.max_attempts, text_response("a", structured=ANALYSIS))
    for _ in range(2):
        await stage.handle(await q.claim_next(run, ["analyze"]))
    assert len(fb.requests) == 2  # a recovered for the second video, but the run's scorer stays b
    async with db_sessionmaker() as s:
        assert {sc.analyst_provider for sc in (await s.execute(select(FindingScore))).scalars()} == {"b"}


async def test_the_scoring_model_moves_once_when_it_hits_its_limit(db_sessionmaker, tmp_path):
    run, q, stage, fa, fb, events, _ = await two_provider_stage(
        db_sessionmaker, tmp_path, [text_response("a", structured=ANALYSIS), UsageLimited("a", "limit", None)],
        [text_response("b", structured=ANALYSIS)])
    for _ in range(2):
        await stage.handle(await q.claim_next(run, ["analyze"]))
    switched = [p for t, p in events if t == "provider.switched"]
    assert len(switched) == 1 and (switched[0]["from"], switched[0]["to"]) == ("a", "b")
    assert stage.analyst_provider == "b"


async def test_the_owners_target_is_judged_even_when_a_measurement_flags_it(db_sessionmaker, tmp_path):
    import dataclasses

    from sqlalchemy import update as sql_update

    flagged = dataclasses.replace(ok_analysis(tmp_path), filtered_reason="multiple_people") \
        if dataclasses.is_dataclass(ok_analysis(tmp_path)) else ok_analysis(tmp_path).model_copy(
            update={"filtered_reason": "multiple_people"})
    run, task_id, q, stage, fa = await setup(db_sessionmaker, tmp_path, flagged,
                                             [text_response("a", structured=ANALYSIS)] * 2)
    await CandidateSink(db_sessionmaker, q).submit(run, task_id, None,
                                                   [Candidate(canonical_id="tiktok:1", why="mine", preliminary_fit=7)])
    async with db_sessionmaker() as s:
        await s.execute(sql_update(Finding).values(source="owner"))
        await s.commit()
    out = await stage.handle(await q.claim_next(run, ["analyze"]))
    async with db_sessionmaker() as s:
        f = (await s.execute(select(Finding))).scalar_one()
        sc = (await s.execute(select(FindingScore))).scalar_one()
    assert "overall" in out and f.status == "analyzed"
    assert sc.feasibility_notes.startswith("Measured: multiple people.")



async def test_the_sink_refuses_old_videos_and_the_owners_own_picks(db_sessionmaker, tmp_path):
    from tf_db.models import ManualVideo, Run

    run, task_id, q, stage, _ = await setup(db_sessionmaker, tmp_path, ok_analysis(tmp_path), [])
    store = VideoStore(db_sessionmaker)
    await store.upsert_videos([
        VideoItem(canonical_id="tiktok:2", platform="tiktok", url="https://www.tiktok.com/@u/video/2",
                  posted_at=NOW - timedelta(days=40)),
        VideoItem(canonical_id="tiktok:3", platform="tiktok", url="https://www.tiktok.com/@u/video/3"),
        VideoItem(canonical_id="tiktok:4", platform="tiktok", url="https://www.tiktok.com/@u/video/4",
                  posted_at=NOW - timedelta(days=3))])
    async with db_sessionmaker() as s:
        await s.execute(update(Run).values(settings={"freshness": "week"}))
        s.add(ManualVideo(url="https://www.tiktok.com/@u/video/4", canonical_id="tiktok:4", platform="tiktok"))
        await s.commit()
    accepted, rejected = await CandidateSink(db_sessionmaker, q).submit(run, task_id, None, [
        Candidate(canonical_id=c, why="fit", preliminary_fit=7) for c in ("tiktok:1", "tiktok:2", "tiktok:3", "tiktok:4")])
    reasons = dict(rejected)
    assert accepted == ["tiktok:1", "tiktok:3"]  # fresh, and no post date known: kept
    assert "posted 40 days ago" in reasons["tiktok:2"] and "manually chosen" in reasons["tiktok:4"]
