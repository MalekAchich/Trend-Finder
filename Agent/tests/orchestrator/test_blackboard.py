import pytest

from tf_agent.orchestrator.blackboard import Blackboard
from tf_agent.tools.types import VideoItem
from tf_db.models import Character, CharacterVersion, Finding, Run, SeenItem, Task, Video


async def make_run(sm, n_tasks=2):
    async with sm() as s:
        c = Character(slug="nicolaiz", name="N", folder_path="/x")
        s.add(c)
        await s.flush()
        v = CharacterVersion(character_id=c.id, version=1, profile_md="p", front_matter={},
                             canonical_image_path="/i.png", content_hash="h")
        s.add(v)
        await s.flush()
        r = Run(character_id=c.id, character_version_id=v.id, state="running")
        s.add(r)
        await s.flush()
        tasks = [Task(run_id=r.id, task_type="scout", platform="tiktok") for _ in range(n_tasks)]
        s.add_all(tasks)
        await s.commit()
        return c.id, r.id, [t.id for t in tasks]


def test_normalize_scope():
    els = Blackboard.normalize_scope("tiktok", {"queries": ["Deadpan  DANCE", "deadpan dance"],
                                                "hashtags": ["#Office"], "creators": ["@Kiana"], "sounds": ["S1"]})
    assert els == [("tiktok", "query", "deadpan dance"), ("tiktok", "hashtag", "office"),
                   ("tiktok", "sound", "s1"), ("tiktok", "creator", "kiana")]


async def test_claims_are_all_or_nothing_per_task(db_sessionmaker):
    bb = Blackboard(db_sessionmaker)
    _, run, (t1, t2) = await make_run(db_sessionmaker)
    assert await bb.claim(run, t1, [("tiktok", "query", "a"), ("tiktok", "hashtag", "b")]) == []
    rejected = await bb.claim(run, t2, [("tiktok", "query", "c"), ("tiktok", "hashtag", "b")])
    assert rejected == [("tiktok", "hashtag", "b")]
    # the non-conflicting element was not kept for t2 either
    assert await bb.claim(run, t2, [("tiktok", "query", "c")]) == []
    assert await bb.owner(run, ("tiktok", "hashtag", "b")) == t1


async def test_seen_filter_hides_submitted_and_rated_only(db_sessionmaker):
    bb = Blackboard(db_sessionmaker)
    char, run, (t1, _) = await make_run(db_sessionmaker)
    async with db_sessionmaker() as s:
        for cid in ("tiktok:1", "tiktok:2", "tiktok:3"):
            s.add(Video(canonical_id=cid, platform="tiktok", url="u"))
        await s.flush()
        s.add(Finding(run_id=run, task_id=t1, canonical_id="tiktok:1"))
        s.add(SeenItem(character_id=char, canonical_id="tiktok:2", rated=True))
        s.add(SeenItem(character_id=char, canonical_id="tiktok:3", rated=False))
        await s.commit()
    items = [VideoItem(canonical_id=f"tiktok:{i}", platform="tiktok", url="u") for i in (1, 2, 3, 4)]
    kept, hidden = await (bb.seen_filter_for(run, char))(items)
    assert [i.canonical_id for i in kept] == ["tiktok:3", "tiktok:4"] and hidden == 2


async def test_leads_dedupe_and_assignment(db_sessionmaker):
    bb = Blackboard(db_sessionmaker)
    _, run, (t1, t2) = await make_run(db_sessionmaker)
    lead = {"type": "sound", "value": "sound:9", "platform": "tiktok", "why": "used by 4 hits"}
    assert await bb.add_leads(run, t1, [lead, lead]) == 1
    open_ = await bb.open_leads(run)
    assert [(l["type"], l["value"]) for l in open_] == [("sound", "sound:9")]
    await bb.assign_lead(open_[0]["id"], t2)
    assert await bb.open_leads(run) == []


async def test_events_are_ordered_and_paginated(db_sessionmaker):
    bb = Blackboard(db_sessionmaker)
    _, run, _ = await make_run(db_sessionmaker)
    ids = [await bb.record_event(run, "task.started", {"i": i}) for i in range(3)]
    assert ids == sorted(ids)
    after = await bb.events_after(run, ids[0])
    assert [e["payload"]["i"] for e in after] == [1, 2] and after[0]["type"] == "task.started"
