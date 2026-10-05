from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from tf_agent.learning.briefs import BriefError, BriefWriter
from tf_agent.models.fake import FakeAdapter, text_response
from tf_agent.roles.runners import Roles
from tf_agent.testing import create_test_run, make_client
from tf_db.models import Brief, CardFeedback, Finding, FindingScore, Run, TrendCluster, Video, VideoAnalysis

BRIEF = {"title": "Gym duty", "concept": "Nicolaiz inspects a gym like a bank", "record_yourself": "walk in, salute",
         "character_orientation": "video", "kling_prompt": "1970s gentleman in cream blazer, plain gym corner",
         "framing": "full body, static camera, 9:16", "motion_window": {"start_s": 0.0, "end_s": 8.0},
         "shots": [{"seconds": 8, "description": "walk-in and salute"}], "risks": ["weights may occlude hands"]}


async def world(sm, tmp_path, rating="up", script=None):
    sheet = tmp_path / "s.jpg"
    sheet.write_bytes(b"x")
    canon = tmp_path / "c.png"
    canon.write_bytes(b"x")
    _, run_id, (task_id,) = await create_test_run(sm)
    async with sm() as s:
        s.add(Video(canonical_id="tiktok:1", platform="tiktok", url="https://www.tiktok.com/@u/video/1"))
        await s.flush()
        s.add(VideoAnalysis(canonical_id="tiktok:1", pipeline_version="1", contact_sheet_path=str(sheet),
                            best_clean_segment={"start_s": 0.0, "end_s": 11.5}, feasibility=80))
        f = Finding(run_id=run_id, task_id=task_id, canonical_id="tiktok:1", status="analyzed")
        s.add(f)
        await s.flush()
        s.add(FindingScore(finding_id=f.id, fit=70, adaptation_idea="gym corner", overall=70))
        c = TrendCluster(run_id=run_id, best_finding_id=f.id, member_count=1, rank=1)
        s.add(c)
        await s.flush()
        if rating:
            s.add(CardFeedback(cluster_id=c.id, rating=rating))
        await s.execute(update(Run).where(Run.id == run_id).values(state="review_ready"))
        await s.commit()
        cluster_id = c.id
    fa = FakeAdapter("a", script or [text_response("a", structured=BRIEF)])
    client, _, _ = make_client({"a": fa})
    character = SimpleNamespace(brief="# Character: N", canonical_image_path=str(canon), version_id=None)
    return cluster_id, BriefWriter(sm, Roles(client)), character, fa


async def test_brief_for_a_liked_card(db_sessionmaker, tmp_path):
    cluster_id, writer, ch, fa = await world(db_sessionmaker, tmp_path)
    brief = await writer.generate(cluster_id, ch)
    assert brief.body["title"] == "Gym duty" and "# Gym duty" in brief.body_md and "0.0s → 8.0s" in brief.body_md
    req = fa.requests[0]
    assert len(req.messages[0].images()) == 2 and "11.5" in req.messages[0].text()


async def test_only_liked_cards_get_briefs(db_sessionmaker, tmp_path):
    cluster_id, writer, ch, _ = await world(db_sessionmaker, tmp_path, rating="down")
    with pytest.raises(BriefError, match="👍"):
        await writer.generate(cluster_id, ch)


async def test_regenerating_replaces_the_brief(db_sessionmaker, tmp_path):
    second = dict(BRIEF, title="Gym duty v2")
    cluster_id, writer, ch, _ = await world(db_sessionmaker, tmp_path, script=[
        text_response("a", structured=BRIEF), text_response("a", structured=second)])
    await writer.generate(cluster_id, ch)
    await writer.generate(cluster_id, ch)
    async with db_sessionmaker() as s:
        rows = (await s.execute(select(Brief))).scalars().all()
    assert len(rows) == 1 and rows[0].body["title"] == "Gym duty v2"
