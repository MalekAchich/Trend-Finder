# Code/00: Architecture Overview

**Status:** Draft v1, awaiting review

## System diagram

```
┌──────────────────────────── FRONTEND (Vite + React) ────────────────────────────┐
│  Characters · New Run · Live Run · Review & Rate · Briefs · Settings            │
└───────────────┬──────────────────────────────────────────────▲──────────────────┘
                │ REST (JSON)                                  │ SSE (live run events)
┌───────────────▼──────────────────────── BACKEND (FastAPI) ────────────────────────┐
│                                                                                   │
│  ORCHESTRATOR (deterministic Python)                                              │
│    run lifecycle · rounds · task queue (Postgres) · worker pool · caps/stop rules │
│    plan validation (scope ownership) · blackboard · event bus → SSE               │
│                                                                                   │
│  AGENT LAYER                                                                      │
│    Master ──plans──► Workers (radar / seed_study / scout / deep_dive) ×N parallel │
│    Analyst (per candidate) · Curator (end of run) · Brief Writer · Learner        │
│                                                                                   │
│  MODEL LAYER                                                                      │
│    ClaudeAdapter · ChatGPTAdapter · (CLI fallback adapters)                       │
│    model registry · role router · usage governor · call ledger                    │
│                                                                                   │
│  TOOL LAYER                                                                       │
│    web_search (SearXNG) · web_fetch · tiktok_* · instagram_* · shorts_* · get_video│
│    each tool: rate limiter · circuit breaker · cache · blackboard filter          │
│                                                                                   │
│  VIDEO PIPELINE (deterministic)                                                   │
│    download · ffprobe · scene-aware frames · contact sheet · whisper · pose       │
│                                                                                   │
│  SCORING & LEARNING                                                               │
│    sub-scores · weighted overall · clustering · direction stats · taste profile   │
└───────┬───────────────────────────────────────────────────────────┬──────────────┘
        │                                                           │
  PostgreSQL 17 + pgvector (Docker)                         media/ (disk, quota-managed)
  state · queue · blackboard · feedback · ledger            videos · frames · contact sheets
        │
  SearXNG (Docker)
```

## Components and responsibilities

| Component | Owns | Does NOT own |
|---|---|---|
| Orchestrator | Run state machine, task queue, concurrency, caps, stop rules, plan validation, events | Any creative/judgment decision |
| Master agent | Dividing the work: directions, tasks, round-by-round re-planning, early stop | Executing tasks, enforcing limits |
| Worker agents | Executing one scoped task with tools; returning candidates and leads | Spawning other workers; scoring final rank |
| Analyst agent | AI judgment of one candidate: character fit, adaptation idea, feasibility notes | Deterministic metrics (pipeline does those) |
| Curator | Clustering, cross-provider check, final ranking, trend cards | Searching |
| Brief Writer | Production brief for a 👍 trend card | Anything before feedback |
| Learner | Taste profile rewrite, direction stats update, weight suggestions | Changing weights without the user's approval |
| Model layer | Talking to providers, choosing models, staying within usage | Prompts/content |
| Tool layer | Platform access, normalization, rate limits, caching | Deciding what to search |
| Video pipeline | Objective measurements of a video | Taste judgments |

Detailed docs: agents → `01`, models → `02`, tools → `03`, pipeline → `04`, scoring/learning → `05`, data → `06`, API/UI → `07`, infra → `08`, tests → `09`.

## Run lifecycle (state machine)

```
created → planning → running(round n) → reviewing_round → [running(round n+1) | curating]
        → review_ready → (user feedback) → learned
Side states: paused_usage (both providers limited; auto-resume) · stopped (user) · failed
```

1. **created:** the user submits New Run. The orchestrator builds the `RunContext`: character brief, seeds, taste profile, direction stats, explore ratio, caps.
2. **planning:** Master call #1 produces a `WorkPlan` (directions + tasks). The orchestrator validates it (scopes, caps) and enqueues the tasks.
3. **running(round n):** workers pull tasks in parallel. Candidates they submit are automatically enqueued for **analysis**: pipeline first, then the Analyst if the candidate passes the feasibility filter. Analysis runs at the same time as searching.
4. **reviewing_round:** when all of the round's tasks finish (or the round time cap hits), the orchestrator builds a `RoundSummary` (hits per direction, best scores, unused leads, failures). Master call #2+ returns more tasks or `stop`. The orchestrator applies its own stop rules first (D-22).
5. **curating:** clustering, then a cross-provider check on the top K, then the final ranking into trend cards.
6. **review_ready:** the user rates findings and the run.
7. **learned:** the Learner has updated the taste profile and direction stats.

## Concurrency model

- One asyncio event loop in the backend process. Agent workers are coroutines; each model call is async I/O.
- **Separate pools** so one kind of work can't starve another:
  - agent pool: max 8 (D/Q-06), also limited by per-provider semaphores (4 each)
  - browser pool: max 2 Playwright contexts (RAM limit)
  - CPU pool: max 2 video pipeline jobs (whisper/pose run in a process pool)
- All task claims go through Postgres (`SKIP LOCKED`), so a restart resumes cleanly.

## Failure handling (summary)

| Failure | Behavior |
|---|---|
| Backend crash/restart | Tasks still marked `running` after their lease expires are re-queued; the run continues |
| One platform scraper broken | Circuit breaker opens; that platform's tasks fail fast with `platform_unavailable`; the Master is told in the next RoundSummary |
| Provider usage limit | Router moves work to the other provider if the role allows; if both are limited, the run goes to `paused_usage` and auto-resumes |
| Worker returns invalid output | One repair retry with the validation error; then the task is marked `failed` with partial results kept |
| Disk quota reached | Oldest unneeded media is evicted first; if still full, new downloads pause and the UI shows a warning |
