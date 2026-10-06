"""The run loop (00-architecture-overview.md): plan → validate/claim → parallel workers + analysis → summary → stop?

The orchestrator is deterministic. Models decide *what* to search (Master) and *how good* a video is (Analyst);
caps, scope ownership, pausing on usage limits and stop rules are enforced here (D-05, D-07, D-22).
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import random
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.characters.folders import LoadedCharacter, load_character
from tf_agent.characters.read import CharacterRead, render_brief
from tf_agent.loop.agent import AgentEvent
from tf_agent.loop.tools import Tool
from tf_agent.models.errors import AllProvidersUnavailable, InvalidRequest
from tf_agent.orchestrator.analysis import AnalysisStage, CandidateSink
from tf_agent.orchestrator.blackboard import Blackboard
from tf_agent.orchestrator.queue import Requeue, TaskQueue, WorkerPool
from tf_agent.pipeline.media import cleanup_run_media
from tf_agent.roles.runners import RoleOutputError, Roles, TaskSpec
from tf_agent.roles.schemas import WorkPlan
from tf_agent.scoring.directions import match_direction_key, normalize_key
from tf_agent.scoring.explore import DirectionStat, explore_ratio, split_tasks, thompson_rank
from tf_agent.scoring.subscores import DEFAULT_WEIGHTS
from tf_agent.tools.normalize import canonical_id
from tf_agent.tools.platforms import SeenFilter
from tf_agent.tools.types import ToolFailure
from tf_db.models import (
    CardFeedback,
    Character,
    Direction,
    Finding,
    FindingScore,
    Round,
    Run,
    RunFeedback,
    ScopeClaim,
    Target,
    TasteProfile,
    Task,
    TrendCluster,
)

log = logging.getLogger(__name__)
WORK_KINDS = ("scout", "radar", "deep_dive")
TERMINAL_STATES = ("review_ready", "stopped", "failed")
STOPPED_BY_OWNER = "stopped by the owner"
RESUMABLE_STATES = ("created", "reading_character", "studying_trends", "planning", "running", "paused_usage",
                    "curating", "interrupted")
FRESHNESS = ("day", "week", "month", "any")
OWNER = {"id": "owner", "role": "owner", "platform": None}


class InputError(ValueError):
    """A run input the owner gave can't be used (bad URL, unknown character)."""


@dataclass
class RunSettings:
    platforms: list[str] = field(default_factory=lambda: ["tiktok", "youtube", "instagram"])
    rounds: int = 3
    tasks_per_round: int = 12
    target_findings: int = 20
    good_score: float = 60.0
    wall_clock_s: float = 3600.0
    workers: int = 8
    analysis_workers: int = 3
    max_candidates: int = 6
    weights: dict[str, float] | None = None  # fixed at creation so a resumed run never mixes weights
    freshness: str = "week"  # how recent searched videos must be: day | week | month | any
    trend_urls: list[str] = field(default_factory=list)  # trending AI-influencer videos to study first
    targets: list[dict[str, str]] = field(default_factory=list)  # [{url, character}] videos the owner found
    used_s: float = 0.0  # active seconds already spent (downtime and usage pauses don't count)


class ActiveClock:
    """Wall-clock budget that only counts active time (not downtime between resumes, not usage pauses)."""

    def __init__(self, budget_s: float) -> None:
        self.start = time.time()
        self.budget = max(budget_s, 0.0)
        self.paused = 0.0
        self._pause_started: float | None = None

    def pause(self) -> None:
        if self._pause_started is None:
            self._pause_started = time.time()

    def resume(self) -> None:
        if self._pause_started is not None:
            self.paused += time.time() - self._pause_started
            self._pause_started = None

    def active(self) -> float:
        now = time.time()
        current_pause = now - self._pause_started if self._pause_started is not None else 0.0
        return now - self.start - self.paused - current_pause

    def remaining(self) -> float:
        return self.budget - self.active()

    def expired(self) -> bool:
        return self.remaining() <= 0


@dataclass
class RunOutcome:
    run_id: uuid.UUID
    state: str
    stop_reason: str | None
    rounds: int
    findings_analyzed: int


def _count(value: Any) -> int:
    """Results carry either lists (accepted, rejected) or counts (leads inserted)."""
    if isinstance(value, int):
        return value
    return len(value) if value else 0


def _json_safe(value: Any, max_chars: int = 400) -> Any:
    """Tool arguments for the stream: plain JSON, long strings clipped."""
    if isinstance(value, dict):
        return {str(k): _json_safe(v, max_chars) for k, v in list(value.items())[:20]}
    if isinstance(value, list | tuple):
        return [_json_safe(v, max_chars) for v in list(value)[:20]]
    if isinstance(value, str):
        return value[:max_chars]
    if value is None or isinstance(value, bool | int | float):
        return value
    return str(value)[:max_chars]


class ToolProvider(Protocol):
    def tools_for(self, platform: str | None, seen_filter: SeenFilter | None = None,
                  recent: str | None = None) -> list[Tool]: ...

    async def get_video(self, url: str) -> Any: ...


class Curator(Protocol):
    async def curate(self, run_id: uuid.UUID, character: LoadedCharacter,
                     weights: dict[str, float] | None = None) -> None: ...


class Orchestrator:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], roles: Roles, tools: ToolProvider,
                 analyzer: Any, store: Any, *, curator: Curator | None = None, weights: dict[str, float] | None = None,
                 learner: Any = None,
                 idle_poll: float = 0.5, monitor_every_s: float = 1.0, rng: random.Random | None = None) -> None:
        self._sm = sessionmaker
        self.roles, self.tools, self.analyzer, self.store = roles, tools, analyzer, store
        self.curator, self.weights, self.learner = curator, weights, learner
        self.idle_poll, self.monitor_every_s = idle_poll, monitor_every_s
        self.rng = rng or random.Random()
        self.blackboard = Blackboard(sessionmaker)
        self.queue = TaskQueue(sessionmaker)
        self.sink = CandidateSink(sessionmaker, self.queue)
        self._providers = itertools.cycle(list(roles.client.adapters) or [None])
        if getattr(roles.client, "on_switch", "absent") is None:
            roles.client.on_switch = self._on_switch

    async def _on_switch(self, ctx: Any, from_provider: str, to_provider: str, reason: str) -> None:
        if ctx.run_id is None:
            return
        agent = {"id": str(ctx.task_id) if ctx.task_id else ctx.role, "role": ctx.role, "platform": None}
        await self.blackboard.record_event(ctx.run_id, "provider.switched", {
            "agent": agent, "from": from_provider, "to": to_provider, "reason": (reason or "")[:200]})

    # ---------- run lifecycle ----------
    async def _last_satisfaction(self, character_id: uuid.UUID) -> int | None:
        """Overall satisfaction the owner gave the character's most recent rated run (D-18)."""
        async with self._sm() as s:
            return (await s.execute(select(RunFeedback.satisfaction).join(Run, Run.id == RunFeedback.run_id).where(
                Run.character_id == character_id).order_by(RunFeedback.updated_at.desc()).limit(1))
                    ).scalar_one_or_none()

    async def _validate_inputs(self, settings: RunSettings) -> list[tuple[uuid.UUID, str, str]]:
        """Check every URL and target character before anything is saved; returns (character_id, url, cid)."""
        if settings.freshness not in FRESHNESS:
            raise InputError(f"freshness must be one of {', '.join(FRESHNESS)}")
        for url in settings.trend_urls:
            if canonical_id(url) is None:
                raise InputError(f"not a TikTok, Instagram or YouTube video URL: {url}")
        targets = []
        for t in settings.targets:
            url, slug = str(t.get("url", "")).strip(), str(t.get("character", "")).strip()
            cid = canonical_id(url)
            if cid is None:
                raise InputError(f"not a TikTok, Instagram or YouTube video URL: {url}")
            async with self._sm() as s:
                char_id = (await s.execute(select(Character.id).where(Character.slug == slug))).scalar_one_or_none()
            if char_id is None:
                raise InputError(f"unknown character for target {url}: {slug}")
            targets.append((char_id, url, cid))
        return targets

    async def create_run(self, slug: str, settings: RunSettings) -> uuid.UUID:
        ch = await load_character(self._sm, slug)
        targets = await self._validate_inputs(settings)
        async with self._sm() as s:
            rated = (await s.execute(select(func.count()).select_from(Direction).where(
                Direction.character_id == ch.character_id, (Direction.alpha + Direction.beta) > 0))).scalar_one()
        ratio = explore_ratio(await self._last_satisfaction(ch.character_id), rated)
        # copy: never mutate the caller's settings
        settings = replace(settings, weights=settings.weights or self.weights or dict(DEFAULT_WEIGHTS))
        inputs = {"freshness": settings.freshness, "trend_urls": list(settings.trend_urls),
                  "targets": [{"url": u, "canonical_id": c, "character_id": str(cid)} for cid, u, c in targets]}
        async with self._sm() as s:
            run = Run(character_id=ch.character_id, character_version_id=ch.version_id, state="created",
                      settings=asdict(settings), inputs=inputs, explore_ratio_used=ratio)
            s.add(run)
            await s.flush()
            for char_id, url, cid in targets:  # owner targets wait for their character's run (this one or a later one)
                stmt = pg_insert(Target).values(character_id=char_id, url=url, canonical_id=cid, status="pending")
                await s.execute(stmt.on_conflict_do_update(index_elements=[Target.character_id, Target.canonical_id],
                                                           set_={"status": "pending", "url": url, "run_id": None}))
            await s.commit()
            run_id = run.id
        await self.blackboard.record_event(run_id, "run.state", {"state": "created", "explore_ratio": ratio})
        return run_id

    async def _set_state(self, run_id: uuid.UUID, state: str, **payload: Any) -> None:
        async with self._sm() as s:
            await s.execute(update(Run).where(Run.id == run_id).values(state=state))
            await s.commit()
        await self.blackboard.record_event(run_id, "run.state", {"state": state, **payload})

    async def _finish(self, run_id: uuid.UUID, state: str, stop_reason: str | None, error: str | None = None) -> None:
        async with self._sm() as s:
            await s.execute(update(Run).where(Run.id == run_id).values(
                state=state, stop_reason=stop_reason, error=error, finished_at=func.now()))
            await s.commit()
        await self.blackboard.record_event(run_id, "run.state", {"state": state, "stop_reason": stop_reason})

    async def _character(self, run: Run) -> LoadedCharacter:
        async with self._sm() as s:
            slug = (await s.execute(select(Character.slug).where(Character.id == run.character_id))).scalar_one()
            read = (await s.execute(select(Run.character_read).where(Run.id == run.id))).scalar_one_or_none()
            taste = (await s.execute(select(TasteProfile.body_md).where(TasteProfile.character_id == run.character_id)
                                     .order_by(TasteProfile.version.desc()).limit(1))).scalar_one_or_none()
        ch = await load_character(self._sm, slug)
        if read:
            ch.brief = render_brief(ch.name, CharacterRead(**read), taste)
        return ch

    async def _outcome(self, run_id: uuid.UUID) -> RunOutcome:
        async with self._sm() as s:
            run = await s.get(Run, run_id)
            analyzed = (await s.execute(select(func.count()).select_from(Finding).where(
                Finding.run_id == run_id, Finding.status == "analyzed"))).scalar_one()
        return RunOutcome(run_id, run.state, run.stop_reason, run.current_round, analyzed)

    async def execute(self, run_id: uuid.UUID) -> RunOutcome:
        """Run (or resume) until a stop rule fires. Safe to call again after a crash or cancellation.

        A Postgres advisory lock guarantees a single loop per run, even across processes (CLI + API).
        """
        async with self._run_lock(run_id) as owned:
            if not owned:
                return await self._outcome(run_id)  # another loop already drives this run
            return await self._execute_locked(run_id)

    @asynccontextmanager
    async def _run_lock(self, run_id: uuid.UUID) -> AsyncIterator[bool]:
        key = int(run_id) & 0x7FFF_FFFF_FFFF_FFFF
        engine = self._sm.kw["bind"]
        async with engine.connect() as lock_conn:
            if not (await lock_conn.execute(text("select pg_try_advisory_lock(:k)"), {"k": key})).scalar_one():
                yield False
                return
            try:
                yield True
            finally:
                async def unlock() -> None:
                    try:
                        await lock_conn.execute(text("select pg_advisory_unlock(:k)"), {"k": key})
                        await lock_conn.commit()
                    except Exception:
                        await lock_conn.invalidate()  # dropping the connection releases the lock too

                await asyncio.shield(unlock())

    async def _execute_locked(self, run_id: uuid.UUID) -> RunOutcome:
        async with self._sm() as s:
            run = await s.get(Run, run_id)
        if run is None:
            raise ValueError(f"unknown run {run_id}")
        if run.state in TERMINAL_STATES:
            return await self._outcome(run_id)
        settings = RunSettings(**run.settings)
        ch = await self._character(run)
        clock = ActiveClock(settings.wall_clock_s - settings.used_s)
        weights = settings.weights or self.weights or dict(DEFAULT_WEIGHTS)
        analysis = AnalysisStage(self._sm, self.analyzer, self.roles, self.store, ch, weights,
                                 emit=self.blackboard.record_event)
        try:
            if run.stop_reason and run.state in ("curating", "interrupted"):
                stop_reason = run.stop_reason  # the stop decision was made before a crash: don't plan again
            else:
                ch = await self._prepare(run_id, settings, clock)
                analysis.character = ch
                run = await self._reload(run_id)
                stop_reason = await self._loop(run, settings, ch, analysis, clock)
                await self._set_stop_decision(run_id, stop_reason)
            await self.queue.cancel_queued(run_id)
            await self._curate(run_id, ch, weights)
            await self._finish(run_id, "review_ready", stop_reason)
        except asyncio.CancelledError:
            raise  # leave the run resumable
        except Exception as e:  # infrastructure trouble: keep everything, allow resume
            log.exception("run %s interrupted", run_id)
            await self._interrupt(run_id, e)
        finally:
            await asyncio.shield(self._save_used(run_id, clock))
        return await self._outcome(run_id)

    async def _reload(self, run_id: uuid.UUID) -> Run:
        async with self._sm() as s:
            return await s.get(Run, run_id)

    async def _with_usage_wait(self, run_id: uuid.UUID, clock: ActiveClock, state: str, call: Any) -> Any:
        """Run a model call; if both subscriptions are limited, pause (not counted) until a reset and retry."""
        while True:
            try:
                return await call()
            except AllProvidersUnavailable as e:
                wait = max((e.earliest_reset or time.time() + 900) - time.time(), 1.0)
                await self._set_state(run_id, "paused_usage", resume_in_s=round(wait))
                clock.pause()
                try:
                    await asyncio.sleep(wait)
                finally:
                    clock.resume()
                await self._set_state(run_id, state)

    async def _save_inputs(self, run_id: uuid.UUID, **values: Any) -> None:
        async with self._sm() as s:
            run = (await s.execute(select(Run).where(Run.id == run_id).with_for_update())).scalar_one()
            await s.execute(update(Run).where(Run.id == run_id).values(inputs={**(run.inputs or {}), **values}))
            await s.commit()

    async def _prepare(self, run_id: uuid.UUID, settings: RunSettings, clock: ActiveClock) -> LoadedCharacter:
        """Phases before the first round; each is recorded so a resumed run skips what's done."""
        run = await self._reload(run_id)
        inputs = run.inputs or {}
        if run.character_read is None or (self.learner is not None and not inputs.get("taste_refreshed")):
            await self._set_state(run_id, "reading_character")
        if self.learner is not None and not inputs.get("taste_refreshed"):
            update_ = await self.learner.refresh_taste(run.character_id, run_id)
            if update_ is not None and update_.version is not None:
                text_ = "Updated what I know about your taste from your latest ratings: " + " ".join(
                    update_.retrospective)
                await self.blackboard.record_event(run_id, "agent.thought", {
                    "agent": {"id": "learner", "role": "learner", "platform": None}, "text": text_[:1200]})
                await self.blackboard.record_event(run_id, "agent.finished", {
                    "agent": {"id": "learner", "role": "learner", "platform": None}, "accepted": 0, "rejected": 0,
                    "leads": 0, "failed": None})
            elif update_ is not None and update_.error:
                await self.blackboard.record_event(run_id, "error", {
                    "agent": {"id": "learner", "role": "learner", "platform": None},
                    "message": f"couldn't update the taste profile this time: {update_.error}"[:300]})
            await self._save_inputs(run_id, taste_refreshed=True)
        if run.character_read is None:
            await self._read_character(run, clock)
        ch = await self._character(await self._reload(run_id))
        await self._study_trends(run_id, settings, ch, clock)
        await self._take_targets(run_id, ch)
        return ch

    async def _read_character(self, run: Run, clock: ActiveClock) -> None:
        ch = await self._character(run)
        async with self._sm() as s:
            taste = (await s.execute(select(TasteProfile.body_md).where(TasteProfile.character_id == run.character_id)
                                     .order_by(TasteProfile.version.desc()).limit(1))).scalar_one_or_none()
            notes = (await s.execute(select(CardFeedback.note).join(TrendCluster, TrendCluster.id == CardFeedback.cluster_id)
                                     .join(Run, Run.id == TrendCluster.run_id)
                                     .where(Run.character_id == run.character_id, CardFeedback.note.is_not(None))
                                     .order_by(CardFeedback.updated_at.desc()).limit(10))).scalars().all()
        taste_body = taste.split("## Owner notes")[0].strip() if taste else None
        judged = await self._with_usage_wait(run.id, clock, "reading_character", lambda: self.roles.read_character(
            ch.name, ch.images, taste_body, list(notes), run_id=run.id))
        read = judged.result.model_dump()
        async with self._sm() as s:
            await s.execute(update(Run).where(Run.id == run.id).values(character_read=read))
            await s.commit()
        await self.blackboard.record_event(run.id, "character.read", {
            "agent": {"id": "reader", "role": "reader", "platform": None}, "read": read,
            "images": len(ch.images), "provider": judged.provider})

    async def _study_trends(self, run_id: uuid.UUID, settings: RunSettings, ch: LoadedCharacter,
                            clock: ActiveClock) -> None:
        studies = dict((await self._reload(run_id)).inputs.get("trend_studies") or {})
        todo = [u for u in settings.trend_urls if u not in studies]
        if not todo:
            return
        await self._set_state(run_id, "studying_trends")
        for i, url in enumerate(todo, 1):
            agent = {"id": f"trend-{len(studies) + 1}", "role": "seed_study", "platform": None}
            await self.blackboard.record_event(run_id, "agent.started", {"agent": agent, "goal": f"study {url}"})
            try:
                item = await self.tools.get_video(url)
                agent["platform"] = item.platform
                result = await self.analyzer.analyze(item)
                if not result.contact_sheet_path:
                    raise ToolFailure("media_unavailable", (result.filtered_reason or "no frames").replace("_", " "))
                from tf_agent.orchestrator.analysis import candidate_facts

                judged = await self._with_usage_wait(
                    run_id, clock, "studying_trends", lambda item=item, result=result: self.roles.study_seed(
                        ch.brief, result.contact_sheet_path, candidate_facts(item, result), run_id=run_id))
                study = judged.result.model_dump()
                await self.blackboard.record_event(run_id, "trend.studied", {"agent": agent, "url": url,
                                                                             "study": study})
                await self.blackboard.record_event(run_id, "agent.finished", {
                    "agent": agent, "accepted": 0, "rejected": 0, "leads": len(study.get("search_angles") or []),
                    "failed": None})
            except (ToolFailure, RoleOutputError, InvalidRequest) as e:
                reason = e.error.message if isinstance(e, ToolFailure) else str(e)[:200]
                study = {"error": reason}
                await self.blackboard.record_event(run_id, "error", {
                    "agent": agent, "message": f"couldn't study {url}: {reason}"[:300]})
                await self.blackboard.record_event(run_id, "agent.finished", {
                    "agent": agent, "accepted": 0, "rejected": 0, "leads": 0, "failed": reason[:200]})
            studies[url] = study
            await self._save_inputs(run_id, trend_studies=studies)

    async def _take_targets(self, run_id: uuid.UUID, ch: LoadedCharacter) -> None:
        """The owner's target videos for this character go straight to analysis (no scout needed)."""
        async with self._sm() as s:
            targets = (await s.execute(select(Target).where(Target.character_id == ch.character_id,
                                                            Target.status == "pending"))).scalars().all()
        for t in targets:
            try:
                item = await self.tools.get_video(t.url)
            except ToolFailure as e:
                await self.blackboard.record_event(run_id, "candidate.rejected", {
                    "agent": OWNER, "canonical_id": t.canonical_id, "reason": f"your target: {e.error.message}"})
            else:
                async with self._sm() as s:
                    stmt = pg_insert(Finding).values(run_id=run_id, canonical_id=item.canonical_id, source="owner",
                                                     why="picked by you").on_conflict_do_nothing()
                    finding_id = (await s.execute(stmt.returning(Finding.id))).scalar_one_or_none()
                    await s.commit()
                if finding_id is not None:
                    await self.queue.enqueue(run_id, None, "analyze", scope={"finding_id": str(finding_id),
                                                                             "canonical_id": item.canonical_id})
            async with self._sm() as s:
                await s.execute(update(Target).where(Target.id == t.id).values(status="used", run_id=run_id))
                await s.commit()

    async def _set_stop_decision(self, run_id: uuid.UUID, stop_reason: str) -> None:
        async with self._sm() as s:
            await s.execute(update(Run).where(Run.id == run_id).values(state="curating", stop_reason=stop_reason))
            await s.commit()
        await self.blackboard.record_event(run_id, "run.state", {"state": "curating", "stop_reason": stop_reason})

    async def _curate(self, run_id: uuid.UUID, ch: LoadedCharacter, weights: dict[str, float] | None) -> None:
        if self.curator is not None:
            await self.curator.curate(run_id, ch, weights=weights)
        try:  # cross-check is done: the contact sheets aren't needed any more
            await cleanup_run_media(self._sm, run_id)
        except Exception as e:  # housekeeping never costs the run
            log.warning("media cleanup for run %s failed: %s", run_id, e)

    async def _save_used(self, run_id: uuid.UUID, clock: ActiveClock) -> None:
        try:
            async with self._sm() as s:
                run = await s.get(Run, run_id)
                if run is not None:
                    settings = dict(run.settings)
                    settings["used_s"] = round(float(settings.get("used_s") or 0) + max(clock.active(), 0.0), 1)
                    await s.execute(update(Run).where(Run.id == run_id).values(settings=settings))
                    await s.commit()
        except Exception as e:
            log.warning("could not record active time for run %s: %s", run_id, e)

    async def stop_and_curate(self, run_id: uuid.UUID) -> RunOutcome:
        """Owner pressed Stop: drop pending work, curate what was found, end `review_ready` (reason kept)."""
        async with self._run_lock(run_id) as owned:
            if not owned:
                return await self._outcome(run_id)  # a loop still owns the run; it will curate itself
            async with self._sm() as s:
                run = await s.get(Run, run_id)
            await self.queue.cancel_queued(run_id)
            await self._set_stop_decision(run_id, STOPPED_BY_OWNER)
            try:
                await self._curate(run_id, await self._character(run), (run.settings or {}).get("weights"))
            except asyncio.CancelledError:
                raise
            except Exception as e:  # resumable: a resume curates again (the stop decision is kept)
                log.exception("curation after stop failed for %s", run_id)
                await self._interrupt(run_id, e)
            else:
                await self._finish(run_id, "review_ready", STOPPED_BY_OWNER)
        return await self._outcome(run_id)

    async def _interrupt(self, run_id: uuid.UUID, error: BaseException) -> None:
        """Mark a run resumable after infrastructure trouble, keeping any stop decision already made."""
        async with self._sm() as s:
            await s.execute(update(Run).where(Run.id == run_id).values(
                state="interrupted", error=f"{type(error).__name__}: {error}"[:500], finished_at=func.now()))
            await s.commit()
            reason = (await s.execute(select(Run.stop_reason).where(Run.id == run_id))).scalar_one_or_none()
        await self.blackboard.record_event(run_id, "run.state", {"state": "interrupted", "stop_reason": reason})

    async def _loop(self, run: Run, settings: RunSettings, ch: LoadedCharacter, analysis: AnalysisStage,
                    clock: ActiveClock) -> str:
        run_id = run.id
        round_no = run.current_round
        if round_no > 0 and not await self._round_has_plan(run_id, round_no):
            round_no -= 1  # a crash between starting and planning a round: plan that same round again
        elif round_no > 0 and await self.queue.outstanding(run_id) > 0:  # resuming mid-round: finish it first
            await self._set_state(run_id, "running", resumed=True)
            if not await self._drain(run_id, ch, settings, analysis, clock):
                return "wall clock limit reached"
            await self._close_round(run_id, run.current_round)
        empty_streak = await self._empty_streak(run_id)
        while True:
            if round_no >= settings.rounds:
                return f"reached the round limit ({settings.rounds})"
            if clock.expired():
                return "wall clock limit reached"
            round_no += 1
            await self._set_state(run_id, "planning", round=round_no)
            round_id = await self._start_round(run_id, round_no)
            context, n_explore, n_exploit = await self._context(run_id, ch, settings, round_no)
            plan = await self._plan(run_id, context, ch, clock)
            if plan is None:
                return "planning failed: no valid plan from the Master"
            if plan.stop and round_no > 1:
                await self._record_round(round_id, plan=plan.model_dump(), rejections=[])
                return f"master stopped: {plan.stop_reason or 'no reason given'}"
            enqueued, rejections = await self._apply_plan(run_id, round_id, plan, settings, ch, n_explore)
            await self._record_round(round_id, plan=plan.model_dump(), rejections=rejections)
            await self.blackboard.record_event(run_id, "plan.created", {
                "agent": {"id": "lead", "role": "lead", "platform": None}, "round": round_no,
                "reasoning": plan.reasoning_summary, "tasks": await self._task_cards(enqueued),
                "refused": [{"goal": r.get("task", "-"), "reason": r["reason"]} for r in rejections],
                "explore": n_explore, "exploit": n_exploit})
            await self._set_state(run_id, "running", round=round_no)
            if not await self._drain(run_id, ch, settings, analysis, clock):
                await self._close_round(run_id, round_no)
                return "wall clock limit reached"
            summary = await self._close_round(run_id, round_no)
            good = await self._good_count(run_id, settings.good_score)
            if good >= settings.target_findings:
                return f"target reached ({good} findings scoring ≥ {settings.good_score:g})"
            empty_streak = empty_streak + 1 if summary["new_analyzed"] == 0 else 0
            if empty_streak >= 2:
                return "no new findings in 2 consecutive rounds"

    # ---------- rounds ----------
    async def _round_has_plan(self, run_id: uuid.UUID, number: int) -> bool:
        async with self._sm() as s:
            plan = (await s.execute(select(Round.plan).where(Round.run_id == run_id, Round.number == number))
                    ).first()
        return plan is not None and plan[0] is not None

    async def _start_round(self, run_id: uuid.UUID, number: int) -> uuid.UUID:
        async with self._sm() as s:
            existing = (await s.execute(select(Round).where(Round.run_id == run_id, Round.number == number))
                        ).scalar_one_or_none()
            rnd = existing or Round(run_id=run_id, number=number)
            if existing is None:
                s.add(rnd)
            await s.execute(update(Run).where(Run.id == run_id).values(current_round=number))
            await s.commit()
            return rnd.id

    async def _record_round(self, round_id: uuid.UUID, **values: Any) -> None:
        mapping = {"plan": "plan", "rejections": "plan_rejections", "summary": "summary"}
        async with self._sm() as s:
            await s.execute(update(Round).where(Round.id == round_id).values(
                **{mapping[k]: v for k, v in values.items()}))
            await s.commit()

    async def _close_round(self, run_id: uuid.UUID, number: int) -> dict[str, Any]:
        async with self._sm() as s:
            rnd = (await s.execute(select(Round).where(Round.run_id == run_id, Round.number == number))
                   ).scalar_one_or_none()
        if rnd is None:
            return {"new_analyzed": 0}
        summary = await self._summarize(run_id, rnd.id, number)
        async with self._sm() as s:
            await s.execute(update(Round).where(Round.id == rnd.id).values(summary=summary, finished_at=func.now()))
            await s.commit()
        await self.blackboard.record_event(run_id, "round.finished", summary)
        return summary

    async def _summarize(self, run_id: uuid.UUID, round_id: uuid.UUID, number: int) -> dict[str, Any]:
        async with self._sm() as s:
            tasks = (await s.execute(select(Task).where(Task.round_id == round_id))).scalars().all()
            task_ids = [t.id for t in tasks]
            rows = (await s.execute(select(Finding, FindingScore, Direction.key).outerjoin(
                FindingScore, FindingScore.finding_id == Finding.id).outerjoin(
                Direction, Direction.id == Finding.direction_id).where(Finding.task_id.in_(task_ids)))).all()
        per_dir: dict[str, dict[str, Any]] = {}
        for f, sc, key in rows:
            d = per_dir.setdefault(key or "unassigned", {"accepted": 0, "analyzed": 0, "filtered": 0,
                                                          "best_overall": None})
            d["accepted"] += 1
            if f.status == "analyzed":
                d["analyzed"] += 1
                if sc and sc.overall is not None:
                    d["best_overall"] = max(d["best_overall"] or 0, sc.overall)
            elif f.status == "filtered_feasibility":
                d["filtered"] += 1
        rejected: dict[str, int] = {}
        notes: list[str] = []
        for t in tasks:
            res = t.result or {}
            for _, reason in res.get("rejected", []):
                rejected[reason] = rejected.get(reason, 0) + 1
            if res.get("notes"):
                notes.append(f"{t.platform}/{t.task_type}: {str(res['notes'])[:200]}")
            if res.get("failed") or t.state == "failed":
                notes.append(f"{t.platform}/{t.task_type} failed: {str(res.get('failed') or t.error)[:160]}")
        return {"round": number, "tasks": {state: sum(1 for t in tasks if t.state == state)
                                           for state in {t.state for t in tasks}},
                "directions": per_dir, "new_analyzed": sum(d["analyzed"] for d in per_dir.values()),
                "rejected_candidates": rejected, "notes": notes[:10]}

    async def _empty_streak(self, run_id: uuid.UUID) -> int:
        async with self._sm() as s:
            summaries = (await s.execute(select(Round.summary).where(Round.run_id == run_id)
                                         .order_by(Round.number.desc()))).scalars().all()
        streak = 0
        for summary in summaries:
            if summary is None or summary.get("new_analyzed", 0) > 0:
                break
            streak += 1
        return streak

    async def _good_count(self, run_id: uuid.UUID, good_score: float) -> int:
        async with self._sm() as s:
            return int((await s.execute(select(func.count()).select_from(Finding).join(
                FindingScore, FindingScore.finding_id == Finding.id).where(
                Finding.run_id == run_id, Finding.status == "analyzed",
                FindingScore.overall >= good_score))).scalar_one())

    # ---------- planning ----------
    def _health(self) -> dict[str, str]:
        registry = getattr(self.tools, "registry", None)
        return registry.snapshot() if registry is not None else {}

    async def _context(self, run_id: uuid.UUID, ch: LoadedCharacter, settings: RunSettings,
                       round_no: int) -> tuple[str, int, int]:
        async with self._sm() as s:
            run = await s.get(Run, run_id)
            dirs = (await s.execute(select(Direction).where(Direction.character_id == ch.character_id))).scalars().all()
            taste = (await s.execute(select(TasteProfile.body_md).where(TasteProfile.character_id == ch.character_id)
                                     .order_by(TasteProfile.version.desc()).limit(1))).scalar_one_or_none()
            owned = (await s.execute(select(ScopeClaim.platform, ScopeClaim.kind, ScopeClaim.value).where(
                ScopeClaim.run_id == run_id).limit(80))).all()
            last = (await s.execute(select(Round).where(Round.run_id == run_id, Round.number == round_no - 1))
                    ).scalar_one_or_none()
        rated = [DirectionStat(d.key, d.alpha, d.beta, d.id, d.label, d.niche) for d in dirs if d.alpha + d.beta > 0]
        ranked = thompson_rank(rated, self.rng)
        n = settings.tasks_per_round
        n_explore, n_exploit = split_tasks(n, run.explore_ratio_used if ranked else 1.0)
        health = self._health()
        leads = await self.blackboard.open_leads(run_id)
        lines = [
            f"Round {round_no} of at most {settings.rounds}.",
            f"Create exactly {n} tasks: {n_explore} explore (new directions) and {n_exploit} exploit "
            f"(existing directions, best first below). Each worker returns up to {settings.max_candidates} candidates.",
            "Platforms enabled: " + ", ".join(f"{p}={health.get(p, 'ok')}" for p in settings.platforms),
            "Niche: not decided yet: spread explore directions over at least 3 distinct niche hypotheses.",
            f"Target: {settings.target_findings} findings scoring ≥ {settings.good_score:g}.",
            "Taste profile (learned from the owner's ratings):\n" + (taste or "none yet: first runs explore broadly."),
        ]
        if ranked:
            lines.append("Existing directions ranked by Thompson sample (key | label | niche | 👍 | 👎):")
            lines += [f"- {d.key} | {d.label} | {d.niche or '-'} | {d.alpha} | {d.beta}" for d, _ in ranked[:15]]
        studies = {u: st for u, st in (run.inputs or {}).get("trend_studies", {}).items() if "error" not in st}
        if studies:
            lines.append("Formats trending in AI-influencer content right now (studied from the owner's examples; "
                         "search for these formats for this character):")
            lines += [f"- {st.get('format')} | hook: {st.get('hook')} | why: {st.get('why_it_works')} | "
                      f"angles: {', '.join(st.get('search_angles') or [])}" for st in list(studies.values())[:8]]
        if leads:
            lines.append("Open leads (use lead_id for deep_dive tasks):")
            lines += [f"- {l['id']} | {l['platform']} {l['type']} = {l['value']} | {l['why'] or ''}" for l in leads]
        if owned:
            lines.append("Scopes already owned this run (never reuse): " + "; ".join(f"{p}/{k}:{v}" for p, k, v in owned))
        if last is not None and last.summary:
            lines.append(f"Previous round summary: {last.summary}")
        if last is not None and last.plan_rejections:
            lines.append(f"Your previous plan had rejected tasks: {last.plan_rejections}")
        return "\n".join(lines), n_explore, n_exploit

    async def _plan(self, run_id: uuid.UUID, context: str, ch: LoadedCharacter,
                    clock: ActiveClock) -> WorkPlan | None:
        for _attempt in range(3):
            try:
                return await self.roles.plan(context, ch.brief, run_id=run_id)
            except AllProvidersUnavailable as e:
                wait = max((e.earliest_reset or time.time() + 900) - time.time(), 1.0)
                await self._set_state(run_id, "paused_usage", resume_in_s=round(wait))
                clock.pause()  # waiting for a usage window doesn't spend the run's time budget
                try:
                    await asyncio.sleep(wait)
                finally:
                    clock.resume()
                await self._set_state(run_id, "planning")
            except RoleOutputError as e:
                log.warning("master output invalid: %s", e)
        return None

    async def _apply_plan(self, run_id: uuid.UUID, round_id: uuid.UUID, plan: WorkPlan, settings: RunSettings,
                          ch: LoadedCharacter, n_explore: int) -> tuple[list[uuid.UUID], list[dict[str, Any]]]:
        rejections: list[dict[str, Any]] = []
        async with self._sm() as s:
            existing = {d.key: d for d in (await s.execute(select(Direction).where(
                Direction.character_id == ch.character_id))).scalars()}
            key_map: dict[str, Direction] = {}
            created: list[Direction] = []
            for d in plan.directions:
                match = match_direction_key(d.key, list(existing))
                row = existing.get(match) if match else None
                if row is None:
                    row = Direction(character_id=ch.character_id, key=normalize_key(d.key), label=d.label,
                                    hypothesis=d.hypothesis, niche=d.niche)
                    s.add(row)
                    await s.flush()
                    existing[row.key] = row
                    created.append(row)
                key_map[d.key] = key_map[normalize_key(d.key)] = row
            await s.commit()
            direction_ids = {k: v.id for k, v in key_map.items()}
            all_ids = {k: v.id for k, v in existing.items()}
            new_ids = {row.id for row in created}

        def resolve(key: str | None) -> tuple[uuid.UUID | None, bool]:
            if not key:
                return None, True
            found = direction_ids.get(key) or direction_ids.get(normalize_key(key))
            if found is None:
                match = match_direction_key(key, list(all_ids))
                found = all_ids.get(match) if match else None
            return found, found is not None
        health = self._health()
        open_leads = {str(l["id"]): l for l in await self.blackboard.open_leads(run_id, limit=200)}
        enqueued: list[uuid.UUID] = []
        explore_count = 0
        for t in plan.tasks[settings.tasks_per_round:]:
            rejections.append({"task": t.goal[:100], "platform": t.platform, "type": t.task_type,
                               "reason": f"over the {settings.tasks_per_round}-task limit for this round"})
        for t in plan.tasks[: settings.tasks_per_round]:
            label = {"task": t.goal[:100], "platform": t.platform, "type": t.task_type}
            if t.platform not in settings.platforms:
                rejections.append({**label, "reason": "platform not enabled for this run"})
                continue
            if health.get(t.platform) in ("unavailable", "needs_login"):
                rejections.append({**label, "reason": f"platform {health[t.platform]}"})
                continue
            scope: dict[str, Any] = t.scope.model_dump()
            lead = None
            if t.task_type == "deep_dive":
                lead = open_leads.get(str(t.lead_id))
                if lead is None:
                    rejections.append({**label, "reason": "deep_dive needs a valid open lead_id"})
                    continue
                scope["lead"] = f"{lead['type']}:{lead['value']}"
                scope["lead_info"] = {k: str(v) for k, v in lead.items()}
            if t.task_type == "radar" and not any(scope.get(k) for k in ("queries", "hashtags")):
                scope["radar"] = "global"
            elements = Blackboard.normalize_scope(t.platform, scope)
            if not elements:
                rejections.append({**label, "reason": "empty scope"})
                continue
            direction_id, known = resolve(t.direction_key)
            if not known:
                rejections.append({**label, "reason": f"unknown direction key {t.direction_key!r}: declare it in "
                                   "`directions` or use an existing key"})
                continue
            task_id = await self.queue.enqueue(run_id, round_id, t.task_type, platform=t.platform,
                                               direction_id=direction_id, scope=scope, goal=t.goal,
                                               budget={"max_candidates": min(t.max_candidates,
                                                                             settings.max_candidates)},
                                               prompt_version=self.roles.prompts.version(t.task_type))
            conflicts = await self.blackboard.claim(run_id, task_id, elements)
            if conflicts:
                async with self._sm() as s:
                    await s.execute(delete(Task).where(Task.id == task_id))
                    await s.commit()
                rejections.append({**label, "reason": "scope already owned by another task: " +
                                   ", ".join(f"{p}/{k}:{v}" for p, k, v in conflicts)})
                continue
            if lead is not None:
                await self.blackboard.assign_lead(uuid.UUID(str(lead["id"])), task_id)
            enqueued.append(task_id)
            explore_count += int(direction_id in new_ids)
        if enqueued and abs(explore_count - n_explore) > 1:
            rejections.append({"task": "-", "platform": "-", "type": "-", "reason":
                               f"warning: {explore_count} explore tasks accepted, {n_explore} were requested"})
        if direction_ids:
            async with self._sm() as s:
                await s.execute(update(Direction).where(Direction.id.in_(set(direction_ids.values()))).values(
                    last_used_at=func.now()))
                await s.commit()
        return enqueued, rejections

    async def _task_cards(self, task_ids: list[uuid.UUID]) -> list[dict[str, Any]]:
        if not task_ids:
            return []
        async with self._sm() as s:
            rows = (await s.execute(select(Task).where(Task.id.in_(task_ids)).order_by(Task.created_at))).scalars()
            return [{"task_id": str(t.id), "role": t.task_type, "platform": t.platform, "goal": t.goal}
                    for t in rows]

    # ---------- execution ----------
    async def _handle_work(self, ch: LoadedCharacter, settings: RunSettings, task: Task) -> dict[str, Any]:
        label = hypothesis = ""
        if task.direction_id is not None:
            async with self._sm() as s:
                d = await s.get(Direction, task.direction_id)
                if d is not None:
                    label, hypothesis = d.label, d.hypothesis or ""
        spec = TaskSpec(task_type=task.task_type, platform=task.platform or "tiktok", scope=task.scope,
                        goal=task.goal or "", max_candidates=int(task.budget.get("max_candidates",
                                                                                 settings.max_candidates)),
                        direction_label=label, direction_hypothesis=hypothesis, lead=task.scope.get("lead_info"))
        recent = settings.freshness if settings.freshness != "any" else None
        tools = self.tools.tools_for(task.platform, self.blackboard.seen_filter_for(task.run_id, ch.character_id),
                                     recent=recent)

        agent = {"id": str(task.id), "role": task.task_type, "platform": task.platform}

        async def emit(type_: str, **payload: Any) -> None:
            await self.blackboard.record_event(task.run_id, type_, {"agent": agent, **payload})

        async def on_event(ev: AgentEvent) -> None:
            if ev.kind == "thought":
                await emit("agent.thought", text=ev.text, step=ev.step)
            elif ev.kind == "tool_call":
                await emit("agent.tool_call", tool=ev.tool, args=_json_safe(ev.args or {}), step=ev.step)
            elif ev.kind == "tool_result":
                await emit("agent.tool_result", tool=ev.tool, summary=ev.text, ok=ev.ok, step=ev.step)

        await emit("agent.started", goal=task.goal)
        try:
            res = await self.roles.work(spec, ch.brief, tools, run_id=task.run_id, task_id=task.id,
                                        prefer=next(self._providers), on_event=on_event)
        except AllProvidersUnavailable as e:
            await emit("agent.finished", accepted=0, rejected=0, leads=0, failed="waiting for a usage reset")
            await emit("error", message="both subscriptions are at their usage limit; this search waits for the reset")
            raise Requeue(datetime.fromtimestamp(e.earliest_reset or time.time() + 900, UTC),
                          "all providers usage-limited") from e
        except asyncio.CancelledError:
            raise
        except Exception as e:  # the stream must always show how a worker ended
            await emit("agent.finished", accepted=0, rejected=0, leads=0, failed=f"{type(e).__name__}: {e}"[:300])
            raise
        if res.status != "succeeded" or res.result is None:
            out = {"failed": res.error, "steps": res.steps, "provider": res.provider}
            await emit("error", message=f"search failed: {res.error}"[:300])
        else:
            cands = res.result.candidates[: spec.max_candidates]
            accepted, rejected = await self.sink.submit(task.run_id, task.id, task.direction_id, cands)
            for cid, reason in rejected:
                await emit("candidate.rejected", canonical_id=cid, reason=reason)
            leads = await self.blackboard.add_leads(task.run_id, task.id,
                                                    [lead.model_dump() for lead in res.result.leads])
            out = {"accepted": accepted, "rejected": [list(r) for r in rejected], "leads": leads,
                   "notes": res.result.notes, "provider": res.provider, "steps": res.steps}
        await emit("agent.finished", accepted=_count(out.get("accepted")), rejected=_count(out.get("rejected")),
                   leads=_count(out.get("leads")), failed=out.get("failed"), notes=out.get("notes"),
                   provider=out.get("provider"))
        return out

    async def _drain(self, run_id: uuid.UUID, ch: LoadedCharacter, settings: RunSettings, analysis: AnalysisStage,
                     clock: ActiveClock) -> bool:
        """Run workers and analysis until the round's work is done. False when the time budget ran out."""
        async def work_handler(task: Task) -> dict[str, Any]:
            return await self._handle_work(ch, settings, task)

        work_pool = WorkerPool(self.queue, run_id, WORK_KINDS, work_handler, settings.workers, idle_poll=self.idle_poll)
        analysis_pool = WorkerPool(self.queue, run_id, ["analyze"], analysis.handle, settings.analysis_workers,
                                   idle_poll=self.idle_poll)
        work_done = asyncio.Event()

        async def run_work() -> None:
            try:
                await work_pool.run_until_idle()
            finally:
                work_done.set()

        async def run_analysis() -> None:
            while True:
                await analysis_pool.run_until_idle()
                if work_done.is_set() and await self.queue.outstanding(run_id, ["analyze"]) == 0:
                    return
                await asyncio.sleep(self.idle_poll)

        async def both() -> None:  # TaskGroup: a crash in one pool cancels the other (no orphaned spending)
            async with asyncio.TaskGroup() as tg:
                tg.create_task(run_work())
                tg.create_task(run_analysis())

        async def monitor() -> None:
            paused = False
            while True:
                await asyncio.sleep(self.monitor_every_s)
                try:
                    async with self._sm() as s:
                        running = (await s.execute(select(func.count()).select_from(Task).where(
                            Task.run_id == run_id, Task.state == "running"))).scalar_one()
                    ready_at = await self.queue.next_ready_at(run_id)
                except Exception as e:
                    log.warning("monitor check failed: %s", e)
                    continue
                waiting = running == 0 and ready_at is not None and ready_at > datetime.now(UTC)
                if waiting and not paused:
                    paused = True
                    clock.pause()
                    await self._set_state(run_id, "paused_usage", resume_at=ready_at.isoformat())
                elif paused and not waiting:
                    paused = False
                    clock.resume()
                    await self._set_state(run_id, "running")

        job = asyncio.create_task(both())
        watcher = asyncio.create_task(monitor())
        try:
            while not job.done():
                remaining = clock.remaining()
                if remaining <= 0:
                    job.cancel()
                    await asyncio.gather(job, return_exceptions=True)
                    return False
                await asyncio.wait({job}, timeout=min(remaining, 1.0))
            job.result()  # re-raise a pool crash (ExceptionGroup) → run becomes `interrupted`
            return True
        except asyncio.CancelledError:
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
            raise
        finally:
            watcher.cancel()
            clock.resume()
