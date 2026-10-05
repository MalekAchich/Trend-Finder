# Plan 3: Orchestrator, Agent Roles, Scoring & Curation: Implementation Plan

> **For agentic workers:** executed inline with superpowers:executing-plans (TDD per task). Task-spec format (owner waived plan review; see the Plan 2 ruling).

**Goal:** `tf run nicolaiz` runs the full multi-agent loop. The Master plans directions and disjoint tasks; workers search in parallel through the Plan 2 tools; every candidate is analyzed (pipeline + vision Analyst); findings are scored, clustered, cross-checked by the other provider and ranked into trend cards in Postgres. Runs are resumable and pause on usage limits.

**Architecture:** `tf_agent.characters` (profile → versions, compact brief, seeds), `tf_agent.scoring` (sub-scores, overall, explore ratio, Thompson sampling, direction matching), `tf_agent.orchestrator` (blackboard, Postgres task queue, worker pool, round loop, stop rules, events), `tf_agent.roles` (schemas, prompts, role runners), `tf_agent.curation` (clustering, cross-check, ranking). Migration `0003` adds the run/character/finding tables.

**Spec:** `Docs/Code docs/00-architecture-overview.md`, `01-agents.md`, `05-scoring-and-learning.md`, `06-data-model.md`, `04-character-profile-format.md`; decisions D-04…D-07, D-17…D-23, D-33.

## Global Constraints

- All Plan 1–2 constraints hold. Defaults (Q-05…Q-08): target 20 cards, 8 workers (4 per provider via the governor), 3 rounds, 12 tasks per round, 60 min wall clock.
- **The orchestrator enforces caps, scope ownership and stop rules; models never do** (D-05, D-07, D-22).
- **Workers never spawn workers;** they return leads (D-06).
- **Candidates must be real:** a submitted canonical ID is accepted only if a tool already returned it (it exists in `videos`). Hallucinated IDs are rejected and counted.
- Analyst and cross-check calls see one **contact sheet** plus the character's **canonical image** (D-16).
- Overall = 0.40·fit + 0.30·feasibility + 0.20·momentum + 0.10·freshness. Weights come from the `settings` table, with defaults in code (D-17).
- Explore ratio: `clamp(0.85 − 0.075·(s−1), 0.15, 0.85)`; first run, or fewer than 3 rated directions, → 1.0 (D-18). Satisfaction feedback arrives in Plan 4; until then runs use 1.0.
- With niche `OPEN`, the Master must spread explore directions over ≥ 3 distinct `niche` hypotheses (D-33).

## Rulings carried in

- **Plan 1 M18** (partial transcript lost on `AllProvidersUnavailable`): a task hitting it is re-queued whole with `not_before = earliest_reset`, and the run moves to `paused_usage`. Redoing a scout step is cheap; this keeps the loop simple.
- **TikTok Creative Center** (`tiktok_trends`) needs Playwright and stays deferred. The **radar** role uses `web_search` + `tiktok_search(recent="week")` instead.
- **Clustering** uses canonical ID, sound ID + frame-hash distance, and frame-hash distance alone. The AI confirmation for borderline cases (D-19) is deferred until real data shows it's needed.

## Review Focus

1. The Master proposes a task whose scope overlaps an earlier task → rejected with a reason, never run twice.
2. A worker submits a canonical ID that no tool ever returned → rejected, not scored.
3. Both providers limited mid-round → run `paused_usage`, tasks re-queued, and the run resumes and completes without duplicate findings.
4. A backend restart mid-run → leases expire, tasks are reclaimed, the run completes.
5. A round produces zero analyzable candidates (all filtered, or the platform down) → the stop rule fires after 2 empty rounds instead of looping until the wall clock.

---

### Task 1: Migration 0003: characters, runs, tasks, blackboard, findings, clusters

`Database/tf_db/models/runs.py` (+ `__init__`), `migrations/versions/0003_runs.py`, tests `Database/tests/test_run_tables.py`.
Tables per 06-data-model.md: `characters`, `character_versions`, `seeds`, `taste_profiles`, `directions` (niche, alpha, beta), `runs`, `rounds`, `tasks` (state, lease_until, not_before, attempts), `scope_claims` (unique run/platform/kind/value), `leads`, `events` (bigserial), `findings` (unique run/canonical_id), `finding_scores`, `trend_clusters`, `trend_members`, `seen_items` (unique character/canonical_id), `settings`. FKs with sensible ON DELETE.
**Tests:** ORM↔migration sync (existing test); unique scope claim; unique finding per run; task queue index exists.

### Task 2: Characters: profile parser, folder sync, compact brief, seeds

`Agent/tf_agent/characters/{profile,sync,brief}.py`; CLI `tf sync-characters`, `tf seed add <slug> <url>`; tests.
- `parse_profile(text) -> Profile(front: dict, sections: dict[str,str], niche_open: bool)`. Front matter is YAML between `---` lines. `niche_open` when the Niche section heading or body contains `OPEN`.
- `sync_characters(dir, sessionmaker)`: upsert characters by slug; a new `character_versions` row when the content hash changes; seeds from `seeds/seeds.txt` and front matter `seeds`.
- `build_brief(profile) -> str` (≤ ~2,400 chars: name, persona, energy, comedic engine, niche or "OPEN: discover", look, Kling constraints, do/don't).
**Tests:** parse the real `Nicolaiz/profile.md`; version bump only on change; brief contains key sections and stays within budget; a missing canonical image → clear error.

### Task 3: Scoring

`Agent/tf_agent/scoring/{subscores,explore,directions}.py`; tests.
- `momentum(item, peers) -> 0–100` = 0.7·pct(views/hour) + 0.3·pct(engagement), with peers being same-platform videos seen in the last 30 days (`peers_for(platform)` SQL helper; with fewer than 20 peers, a log-scaled absolute fallback).
- `freshness(age_hours, saturation_pct=0) -> 0–100`.
- `overall(sub, weights)`.
- `explore_ratio(satisfaction | None, rated_directions)`, `thompson_rank(directions, rng)`.
- `match_direction_key(key, existing_keys) -> existing | None` (normalized, difflib ≥ 0.85).
**Tests:** formula values; percentile edges; fallback without peers; explore-ratio table from the spec; Thompson determinism with a seed; key matching.

### Task 4: Blackboard

`Agent/tf_agent/orchestrator/blackboard.py`; tests (DB).
- `normalize_scope(platform, scope) -> list[(platform, kind, value)]`.
- `claim(run, task, elements) -> rejected_elements` (atomic, unique constraint).
- `seen_filter(run_id, character_id)` → hides IDs already submitted in this run or rated in earlier runs.
- `add_leads`, `take_leads`, `record_event(run_id, type, payload)`.
**Tests:** an overlapping claim is rejected and the first owner kept; the seen filter hides submitted/rated IDs only; events get ordered IDs.

### Task 5: Role schemas, prompts and runners

`Agent/tf_agent/roles/{schemas,prompts,runners}.py`, `Agent/tf_agent/prompts/*.md` (master, scout, radar, deep_dive, seed_study, analyst, cross_check); tests with FakeAdapter.
- Schemas: `WorkPlan` (reasoning_summary, directions[], tasks[], stop, stop_reason); `WorkerResult` (candidates[{canonical_id, why, preliminary_fit 0–10}], leads[{type, value, platform, why}], notes); `SeedStudy`; `AnalystResult` (fit_breakdown{persona, deadpan_contrast, energy, niche, adaptability} 0–10, justification, adaptation_idea, feasibility_notes, niche_guess); `CrossCheck` (fit 0–100, justification).
- Runners: `plan(ctx) -> WorkPlan` (structured, role master); `run_worker(task, tools) -> WorkerResult` (run_agent with the platform's tools + web tools); `analyze_candidate(item, analysis, brief, canonical_image) -> AnalystResult` (structured + 2 images); `cross_check(...)` (role cross_check, `only=` the other provider).
**Tests:** prompts render with the brief and contain the scope boundary; each runner validates its schema; the analyst request carries exactly 2 images; cross-check forces the other provider.

### Task 6: Postgres task queue and worker pool

`Agent/tf_agent/orchestrator/queue.py`; tests (DB).
- `enqueue`, `claim_next(run_id, kinds, worker)` (FOR UPDATE SKIP LOCKED; respects `not_before` and expired leases), `renew`, `complete`, `fail`, `requeue(not_before)`, `round_outstanding(run_id, round)`.
- `WorkerPool(n)` runs claim → handler → complete, with lease renewal every 2 min.
**Tests:** two concurrent claimers never get the same task; an expired lease is reclaimed; `not_before` is respected; a failing handler marks the task failed and the pool continues.

### Task 7: Analysis stage

`Agent/tf_agent/orchestrator/analysis.py`; tests with a fake analyzer and FakeAdapter.
- On every accepted candidate: finding `pending_analysis` → enqueue `analyze`.
- Handler: VideoAnalyzer → filtered → `filtered_feasibility`; else analyst → `finding_scores` (fit, feasibility, momentum, freshness, overall) → `analyzed`.
**Tests:** happy path writes scores; filtered videos skip the analyst; a hallucinated ID is rejected; analyst failure → finding `failed`, not a crashed run.

### Task 8: Orchestrator: rounds, plan validation, stop rules, pause/resume, events

`Agent/tf_agent/orchestrator/run.py`; CLI `tf run`, `tf runs`, `tf resume`; integration tests (FakeAdapter scripted by role, fake tools, fake analyzer).
- `RunContext` builder (brief, canonical image, seeds, taste profile, Thompson-ranked directions, explore ratio, platform health, caps, provider capacity).
- `validate_plan` (cap tasks, explore share ±1, niche spread when OPEN, scope claims, direction upsert/merge) → rejections recorded and fed back.
- Round loop: plan → enqueue → workers + analysis drain → `RoundSummary` → stop rules → re-plan.
- `AllProvidersUnavailable` → `paused_usage` + requeue, auto-resume.
**Tests:** a full happy-path run reaches `review_ready` with no duplicate clusters; overlapping scopes are rejected; two empty rounds stop the run; pause and resume; restart recovery via expired leases.

### Task 9: Curation and trend cards; live run

`Agent/tf_agent/curation/{cluster,curate}.py`; CLI `tf trends <run>`; tests; live run.
- Clustering (canonical ID; sound + frame-hash distance < 12; frame-hash < 8), best source (feasibility → resolution → momentum), freshness saturation update, top-15 cross-check (disagreement ≥ 30 flagged, fit averaged), ranking → `trend_clusters` / `trend_members`.
**Tests:** reposts cluster; the best source is chosen correctly; disagreement flag; ranking order.
**Live:** `tf run nicolaiz --rounds 1 --tasks 3 --target 5` on both subscriptions produces ranked trend cards.
