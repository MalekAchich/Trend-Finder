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
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.characters.sync import LoadedCharacter, load_character
from tf_agent.loop.agent import AgentEvent
from tf_agent.loop.tools import Tool
from tf_agent.models.errors import AllProvidersUnavailable
from tf_agent.orchestrator.analysis import AnalysisStage, CandidateSink
from tf_agent.orchestrator.blackboard import Blackboard
from tf_agent.orchestrator.queue import Requeue, TaskQueue, WorkerPool
from tf_agent.roles.runners import RoleOutputError, Roles, TaskSpec
from tf_agent.roles.schemas import WorkPlan
from tf_agent.scoring.directions import match_direction_key, normalize_key
from tf_agent.scoring.explore import DirectionStat, explore_ratio, split_tasks, thompson_rank
from tf_agent.tools.platforms import SeenFilter
from tf_db.models import (
    Character,
    Direction,
    Finding,
    FindingScore,
    Round,
    Run,
    RunFeedback,
    ScopeClaim,
    Seed,
    Setting,
    TasteProfile,
    Task,
)

log = logging.getLogger(__name__)
WORK_KINDS = ("scout", "radar", "deep_dive")
TERMINAL_STATES = ("review_ready", "stopped", "failed")


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


@dataclass
class RunOutcome:
    run_id: uuid.UUID
    state: str
    stop_reason: str | None
    rounds: int
    findings_analyzed: int


class ToolProvider(Protocol):
    def tools_for(self, platform: str | None, seen_filter: SeenFilter | None = None) -> list[Tool]: ...


class Curator(Protocol):
    async def curate(self, run_id: uuid.UUID, character: LoadedCharacter) -> None: ...


class Orchestrator:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], roles: Roles, tools: ToolProvider,
                 analyzer: Any, store: Any, *, curator: Curator | None = None, weights: dict[str, float] | None = None,
                 idle_poll: float = 0.5, monitor_every_s: float = 1.0, rng: random.Random | None = None) -> None:
        self._sm = sessionmaker
        self.roles, self.tools, self.analyzer, self.store = roles, tools, analyzer, store
        self.curator, self.weights = curator, weights
        self.idle_poll, self.monitor_every_s = idle_poll, monitor_every_s
        self.rng = rng or random.Random()
        self.blackboard = Blackboard(sessionmaker)
        self.queue = TaskQueue(sessionmaker)
        self.sink = CandidateSink(sessionmaker, self.queue)
        self._providers = itertools.cycle(list(roles.client.adapters) or [None])

    # ---------- run lifecycle ----------
    async def _last_satisfaction(self, character_id: uuid.UUID) -> int | None:
        """Overall satisfaction the owner gave the character's most recent rated run (D-18)."""
        async with self._sm() as s:
            return (await s.execute(select(RunFeedback.satisfaction).join(Run, Run.id == RunFeedback.run_id).where(
                Run.character_id == character_id).order_by(RunFeedback.updated_at.desc()).limit(1))
                    ).scalar_one_or_none()

    async def _saved_weights(self) -> dict[str, float] | None:
        """Scoring weights the owner saved in Settings (None → defaults)."""
        async with self._sm() as s:
            return (await s.execute(select(Setting.value).where(Setting.key == "weights"))).scalar_one_or_none()

    async def create_run(self, slug: str, settings: RunSettings) -> uuid.UUID:
        ch = await load_character(self._sm, slug)
        async with self._sm() as s:
            rated = (await s.execute(select(func.count()).select_from(Direction).where(
                Direction.character_id == ch.character_id, (Direction.alpha + Direction.beta) > 0))).scalar_one()
        ratio = explore_ratio(await self._last_satisfaction(ch.character_id), rated)
        async with self._sm() as s:
            run = Run(character_id=ch.character_id, character_version_id=ch.version_id, state="created",
                      settings=asdict(settings), explore_ratio_used=ratio)
            s.add(run)
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
        return await load_character(self._sm, slug)

    async def _outcome(self, run_id: uuid.UUID) -> RunOutcome:
        async with self._sm() as s:
            run = await s.get(Run, run_id)
            analyzed = (await s.execute(select(func.count()).select_from(Finding).where(
                Finding.run_id == run_id, Finding.status == "analyzed"))).scalar_one()
        return RunOutcome(run_id, run.state, run.stop_reason, run.current_round, analyzed)

    async def execute(self, run_id: uuid.UUID) -> RunOutcome:
        """Run (or resume) until a stop rule fires. Safe to call again after a crash or cancellation."""
        async with self._sm() as s:
            run = await s.get(Run, run_id)
        if run is None:
            raise ValueError(f"unknown run {run_id}")
        if run.state in TERMINAL_STATES:
            return await self._outcome(run_id)
        settings = RunSettings(**run.settings)
        ch = await self._character(run)
        deadline = run.started_at.timestamp() + settings.wall_clock_s
        analysis = AnalysisStage(self._sm, self.analyzer, self.roles, self.store, ch,
                                 self.weights or await self._saved_weights())
        try:
            stop_reason = await self._loop(run, settings, ch, analysis, deadline)
            await self.queue.cancel_queued(run_id)
            if self.curator is not None:
                await self._set_state(run_id, "curating")
                await self.curator.curate(run_id, ch)
            await self._finish(run_id, "review_ready", stop_reason)
        except asyncio.CancelledError:
            raise  # leave the run resumable
        except Exception as e:
            log.exception("run %s failed", run_id)
            await self._finish(run_id, "failed", None, error=f"{type(e).__name__}: {e}"[:500])
        return await self._outcome(run_id)

    async def _loop(self, run: Run, settings: RunSettings, ch: LoadedCharacter, analysis: AnalysisStage,
                    deadline: float) -> str:
        run_id = run.id
        if await self.queue.outstanding(run_id) > 0:  # resuming mid-round: finish that round's work first
            await self._set_state(run_id, "running", resumed=True)
            if not await self._drain(run_id, ch, settings, analysis, deadline):
                return "wall clock limit reached"
            await self._close_round(run_id, run.current_round)
        round_no = run.current_round
        empty_streak = await self._empty_streak(run_id)
        while True:
            if round_no >= settings.rounds:
                return f"reached the round limit ({settings.rounds})"
            if time.time() >= deadline:
                return "wall clock limit reached"
            round_no += 1
            await self._set_state(run_id, "planning", round=round_no)
            round_id = await self._start_round(run_id, round_no)
            context, n_explore, n_exploit = await self._context(run_id, ch, settings, round_no)
            plan = await self._plan(run_id, context, ch, deadline)
            if plan is None:
                return "planning failed: no valid plan from the Master"
            if plan.stop and round_no > 1:
                await self._record_round(round_id, plan=plan.model_dump(), rejections=[])
                return f"master stopped: {plan.stop_reason or 'no reason given'}"
            enqueued, rejections = await self._apply_plan(run_id, round_id, plan, settings, ch, n_explore)
            await self._record_round(round_id, plan=plan.model_dump(), rejections=rejections)
            await self.blackboard.record_event(run_id, "plan.created", {
                "round": round_no, "summary": plan.reasoning_summary, "tasks": len(enqueued),
                "rejections": rejections, "explore": n_explore, "exploit": n_exploit})
            await self._set_state(run_id, "running", round=round_no)
            if not await self._drain(run_id, ch, settings, analysis, deadline):
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
    async def _start_round(self, run_id: uuid.UUID, number: int) -> uuid.UUID:
        async with self._sm() as s:
            rnd = Round(run_id=run_id, number=number)
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
            seeds = (await s.execute(select(Seed.url, Seed.study).where(Seed.character_id == ch.character_id))).all()
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
            "Niche: OPEN: spread explore directions over at least 3 distinct niche hypotheses."
            if ch.profile.niche_open else "Niche: defined in the character brief.",
            f"Target: {settings.target_findings} findings scoring ≥ {settings.good_score:g}.",
            "Taste profile (learned from the owner's ratings):\n" + (taste or "none yet: first runs explore broadly."),
        ]
        if ranked:
            lines.append("Existing directions ranked by Thompson sample (key | label | niche | 👍 | 👎):")
            lines += [f"- {d.key} | {d.label} | {d.niche or '-'} | {d.alpha} | {d.beta}" for d, _ in ranked[:15]]
        if seeds:
            lines.append("Seed videos from the owner:")
            lines += [f"- {url}" + (f": {study}" if study else "") for url, study in seeds[:10]]
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

    async def _plan(self, run_id: uuid.UUID, context: str, ch: LoadedCharacter, deadline: float) -> WorkPlan | None:
        for _attempt in range(3):
            try:
                return await self.roles.plan(context, ch.brief, run_id=run_id)
            except AllProvidersUnavailable as e:
                wait = min(max((e.earliest_reset or time.time() + 900) - time.time(), 1.0), deadline - time.time())
                if wait <= 0:
                    return None
                await self._set_state(run_id, "paused_usage", resume_in_s=round(wait))
                await asyncio.sleep(wait)
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
            for d in plan.directions:
                match = match_direction_key(d.key, list(existing))
                row = existing.get(match) if match else None
                if row is None:
                    row = Direction(character_id=ch.character_id, key=normalize_key(d.key), label=d.label,
                                    hypothesis=d.hypothesis, niche=d.niche)
                    s.add(row)
                    await s.flush()
                    existing[row.key] = row
                key_map[d.key] = key_map[normalize_key(d.key)] = row
            await s.commit()
            direction_ids = {k: v.id for k, v in key_map.items()}
        health = self._health()
        open_leads = {str(l["id"]): l for l in await self.blackboard.open_leads(run_id, limit=200)}
        enqueued: list[uuid.UUID] = []
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
            direction_id = direction_ids.get(t.direction_key or "") or direction_ids.get(
                normalize_key(t.direction_key or ""))
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
        if direction_ids:
            async with self._sm() as s:
                await s.execute(update(Direction).where(Direction.id.in_(set(direction_ids.values()))).values(
                    last_used_at=func.now()))
                await s.commit()
        return enqueued, rejections

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
        tools = self.tools.tools_for(task.platform, self.blackboard.seen_filter_for(task.run_id, ch.character_id))

        async def on_event(ev: AgentEvent) -> None:
            await self.blackboard.record_event(task.run_id, "task.progress", {
                "task_id": str(task.id), "kind": ev.kind, "step": ev.step, "detail": ev.detail[:200]})

        try:
            res = await self.roles.work(spec, ch.brief, tools, run_id=task.run_id, task_id=task.id,
                                        prefer=next(self._providers), on_event=on_event)
        except AllProvidersUnavailable as e:
            raise Requeue(datetime.fromtimestamp(e.earliest_reset or time.time() + 900, UTC),
                          "all providers usage-limited") from e
        if res.status != "succeeded" or res.result is None:
            out = {"failed": res.error, "steps": res.steps, "provider": res.provider}
        else:
            cands = res.result.candidates[: spec.max_candidates]
            accepted, rejected = await self.sink.submit(task.run_id, task.id, task.direction_id, cands)
            leads = await self.blackboard.add_leads(task.run_id, task.id,
                                                    [lead.model_dump() for lead in res.result.leads])
            out = {"accepted": accepted, "rejected": [list(r) for r in rejected], "leads": leads,
                   "notes": res.result.notes, "provider": res.provider, "steps": res.steps}
        await self.blackboard.record_event(task.run_id, "task.finished", {"task_id": str(task.id),
                                                                         "type": task.task_type, **out})
        return out

    async def _drain(self, run_id: uuid.UUID, ch: LoadedCharacter, settings: RunSettings, analysis: AnalysisStage,
                     deadline: float) -> bool:
        """Run workers and analysis until the round's work is done. False when the wall clock ran out."""
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

        async def monitor() -> None:
            paused = False
            while True:
                await asyncio.sleep(self.monitor_every_s)
                async with self._sm() as s:
                    running = (await s.execute(select(func.count()).select_from(Task).where(
                        Task.run_id == run_id, Task.state == "running"))).scalar_one()
                ready_at = await self.queue.next_ready_at(run_id)
                waiting = running == 0 and ready_at is not None and ready_at > datetime.now(UTC)
                if waiting and not paused:
                    paused = True
                    await self._set_state(run_id, "paused_usage", resume_at=ready_at.isoformat())
                elif paused and not waiting:
                    paused = False
                    await self._set_state(run_id, "running")

        watcher = asyncio.create_task(monitor())
        try:
            await asyncio.wait_for(asyncio.gather(run_work(), run_analysis()), max(deadline - time.time(), 0.1))
            return True
        except TimeoutError:
            return False
        finally:
            watcher.cancel()
