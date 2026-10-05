from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from tf_agent.characters.profile import Profile
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

ANALYSIS = {"fit_breakdown": {"persona": 8, "deadpan_contrast": 9, "energy": 7, "niche": 6, "adaptability": 8},
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
    character = SimpleNamespace(character_id=char_id, brief="# Character: N", canonical_image_path=str(canon),
                                profile=Profile(front={}))
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
    assert sc.fit == pytest.approx(76.0) and sc.feasibility == 80.0 and sc.freshness == 100.0
    assert sc.momentum is not None and sc.overall is not None and sc.analyst_provider == "a"
    assert [i.path for i in fa.requests[0].messages[0].images()][0].endswith("canon.png")


async def test_filtered_video_skips_the_analyst(db_sessionmaker, tmp_path):
    filtered = VideoAnalysisResult.filtered("tiktok:1", "multiple_people")
    run, task_id, q, stage, fa = await setup(db_sessionmaker, tmp_path, filtered, [])
    out = await run_one(db_sessionmaker, q, stage, run, task_id)
    async with db_sessionmaker() as s:
        f = (await s.execute(select(Finding))).scalar_one()
    assert f.status == "filtered_feasibility" and out == {"filtered": "multiple_people"} and fa.requests == []


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
