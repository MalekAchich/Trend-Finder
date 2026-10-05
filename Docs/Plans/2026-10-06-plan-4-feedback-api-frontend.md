# Plan 4: Feedback & Learning, Briefs, API + Live Events, Web App: Implementation Plan

> **For agentic workers:** executed inline with superpowers:executing-plans (TDD per task). Task-spec format (owner waived plan review).

**Goal:** The owner runs everything from a web app. They pick a character, start a run, watch the agents work live, rate the trend cards (👍/👎/skip, note, overall satisfaction), get production briefs for the 👍 ones, and the system learns: taste profile, direction stats, explore ratio, weight suggestions.

**Architecture:** Migration `0004` adds `card_feedback`, `run_feedback` and `briefs`. `tf_agent.learning` holds the Learner (direction Beta updates, seen-items, LLM taste-profile rewrite, logistic-regression weight suggestion) and the Brief writer. `tf_backend` gains a `RunManager` (background runs in the API process, auto-resume on start), REST routes, SSE events and safe media serving. `Frontend/` is a Vite + React + TypeScript + Tailwind app served by Vite in dev and as static files by FastAPI in production.

**Spec:** `Docs/Code docs/05-scoring-and-learning.md`, `07-api-and-frontend.md`, `06-data-model.md`; decisions D-09, D-18, D-21; Plan 1 M16 (mutating routes require `x-trendfinder-client`).

## Global Constraints

- All earlier constraints hold.
- Every mutating API route requires the `x-trendfinder-client` header. The frontend always sends it.
- Media is served only from `MEDIA_DIR` and the characters folder, path-validated (no traversal).
- Feedback is idempotent per card (latest rating wins). Learning runs once per submission.
- Weight suggestions are **shown, never auto-applied** (D-17/05). Applying them is an explicit settings change.
- Briefs exist only for 👍 cards (D-21).
- The UI works on desktop and at phone width; keyboard review shortcuts J/K/U/D/S/N.

## Review Focus

1. Rating the same card twice (👍 then 👎) → stats reflect only the final rating, not both.
2. The browser reconnects to the live run (SSE) after a drop → no duplicated or lost events (Last-Event-ID replay).
3. The backend restarts while a run is going → the run resumes automatically, and the UI reconnects.
4. A media path like `../../secrets/chatgpt-auth.json` → 404, never a file.
5. Feedback submitted for a run that's still running → rejected with a clear message (rate only `review_ready` runs).

---

### Task 1: Migration 0004 + feedback model
Tables `card_feedback` (cluster_id unique, rating, note), `run_feedback` (run_id PK, satisfaction 1–10, note), `briefs` (cluster_id unique, character_version_id, body jsonb, body_md, provider, model). Hook up `Orchestrator._last_satisfaction` to read the latest `run_feedback` for the character.
**Tests:** ORM↔migration sync; the latest satisfaction feeds `create_run`'s explore ratio.

### Task 2: Learner
`tf_agent/learning/{learner,weights}.py`, prompt `learner.md`, schema `LearnerResult(taste_profile_md, retrospective[3–5], primary_niche|None)`.
- `apply_feedback(run_id, cards, satisfaction, note)`: upsert ratings; recompute direction alpha/beta for the run's directions from *final* card ratings (idempotent: per-run contributions are stored and replaced); mark cluster members `seen_items.rated=true`; run the LLM learner (taste profile ≤ 500 words, owner notes kept verbatim) → new `taste_profiles` version; weight suggestion once ≥ 30 rated cards (logistic regression on the 4 sub-scores, numpy, L2, non-negative normalized weights).
**Tests:** re-rating replaces earlier contributions; seen-items marked; taste-profile version increments with notes preserved; weight suggestion returns None below 30 and sensible weights on synthetic data; learner failure leaves ratings saved.

### Task 3: Production brief writer
Prompt `brief.md`, schema `Brief(title, concept, record_yourself, character_orientation video|image, kling_prompt, framing, motion_window{start_s,end_s}, shots[{seconds, description}], risks[])`. `generate_brief(cluster_id)` → 👍 only, uses the canonical image + contact sheet + facts + adaptation idea + best clean segment, stores JSON + markdown.
**Tests:** non-👍 rejected; brief stored with markdown rendering; regenerate replaces.

### Task 4: RunManager + REST API + SSE
`tf_backend/runs.py` (start/stop/resume runs as background tasks in the API process; auto-resume unfinished runs on startup), routes: characters (list/detail/sync/taste-profile GET+PUT/seeds POST), runs (POST, GET list/detail with rounds+tasks, POST stop/resume), `GET /api/runs/{id}/events` (SSE with Last-Event-ID), trends (`GET /api/runs/{id}/trends?include=filtered`), feedback (POST), briefs (POST/GET), platforms (GET), settings (GET/PUT weights + suggestion), media (`GET /api/media/{kind}/{path}`).
**Tests (ASGI, fake orchestrator):** each route's happy path and errors; SSE replay from Last-Event-ID; header required on POST/PUT; media traversal blocked; feedback on a non-review_ready run rejected.

### Task 5: Web app
`Frontend/` Vite + React + TS + Tailwind; pages: Characters, New Run, Live Run (rounds timeline, Master reasoning, worker grid, findings feed, provider usage), Review (trend cards: video link, contact sheet, sub-score bars, justification, adaptation idea, motion window, disagreement flag, 👍/👎/skip + note, satisfaction 1–10, keyboard shortcuts), Briefs (copy buttons), Settings (weights + suggestion, providers, platforms). FastAPI serves `Frontend/dist` at `/`.
**Tests:** vitest for the API client and the review keyboard reducer; `npm run build` passes; an end-to-end smoke in a real browser against the running backend.

### Task 6: Live end-to-end + docs
`tf serve` (API + built UI). A live run from the UI; rate it; the learner updates; a brief is generated. Update `Docs/` (README quick start, 07, 08) and the Plans index.
