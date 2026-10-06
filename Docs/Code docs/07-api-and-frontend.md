# Code/07: API & Frontend

**Status:** Built (Plan 4). This page describes the shipped API and UI.

Everything is served by one process: `tf serve` runs the API and the built web app on `http://127.0.0.1:8000` and refuses to bind a non-loopback interface unless you pass `--allow-remote` (there is no login). Runs execute inside the same process as background tasks. Unfinished runs (`created`, `planning`, `running`, `paused_usage`, `curating`, `interrupted`) resume on startup.

Every mutating route (POST/PUT) requires the `x-trendfinder-client` header. The web app always sends it, which stops cross-site form posts.

## REST API (`/api`)

| Method & path | Purpose |
|---|---|
| `GET /health` | Liveness |
| `GET /characters` · `GET /characters/{slug}` | List / detail (version, brief, sections, seeds, latest taste profile, direction stats) |
| `POST /characters/sync` | Re-scan `CHARACTERS_DIR`; a changed `profile.md` creates a new character version |
| `PUT /characters/{slug}/taste-profile` | Owner edit; creates a new taste-profile version (`author=owner`) |
| `POST /characters/{slug}/seeds` | Add a seed video URL (TikTok, Instagram or YouTube) |
| `POST /runs` | Start a run `{slug, platforms, rounds, tasks_per_round, target_findings, good_score, minutes, workers}` |
| `GET /runs` · `GET /runs/{id}` | List / detail (state, rounds with the Master's reasoning and rejections, tasks, finding counts) |
| `POST /runs/{id}/stop` | Stop now and rank what was found (curates; ends in `review_ready`) |
| `POST /runs/{id}/resume` | Resume a run that isn't active (409 for finished runs) |
| `GET /runs/{id}/events` | **SSE**; resumes from `Last-Event-ID` (or `?after=`), heartbeat every 15 s, closes after the run ends |
| `GET /runs/{id}/trends?include=filtered` | Ranked trend cards with scores, analysis, cross-check, feedback and brief status, plus filtered videos and run feedback |
| `POST /runs/{id}/feedback` | `{cards: [{cluster_id, rating: up\|down\|skip, note?}], satisfaction: 1–10, note?}`. Only for `review_ready` runs (409 otherwise). Idempotent: re-rating replaces the earlier contribution. Runs the Learner and returns the taste-profile version, retrospective, niche guess, weight suggestion and any learner error. Ratings are saved even if the Learner fails |
| `POST /trends/{id}/brief` · `GET /trends/{id}/brief` | Write (or rewrite) / read a production brief. 👍 cards only (409 otherwise) |
| `GET /briefs` | Latest 100 briefs |
| `GET /providers` · `GET /providers/{p}/models` | Subscription status (connected, ok/cooling/auth_error, usage %, reset time), model lists |
| `POST /providers/chatgpt/login/start` · `/claude/login/start` · `/claude/login/complete` · `/{p}/logout` | Subscription logins |
| `GET /platforms` | Platform health: `ok`, `degraded`, `unavailable`, `needs_login` |
| `GET /settings` · `PUT /settings` | Scoring weights (must sum to 1) + the Learner's suggestion. The suggestion is never auto-applied |
| `GET /media/{kind}/{path}` | Contact sheets, videos, character images; confined to `MEDIA_DIR` / `CHARACTERS_DIR` (traversal → 404) |

Any other non-`/api` GET serves the web app (`index.html` for client routes, so deep links and reloads work). Unknown `/api` paths stay JSON 404s.

## SSE event types

`run.state` (`{state, stop_reason?, explore_ratio?}`), `plan.created` (`{round, summary, tasks, rejections, explore, exploit}`), `task.progress` (`{task_id, kind, step, detail}`), `task.finished` (`{task_id, type, accepted, rejected, leads, …}` or `{failed}`), `round.finished` (the round summary: per-direction counts, `new_analyzed`, notes).

## Web app (`Frontend/`: Vite + React 19 + TypeScript + Tailwind 4 + TanStack Query)

The design is an editor's light table. Contact sheets are the hero, and the owner's verdicts are grease-pencil marks: a red circle for a select and a strike for a pass. Type is Archivo, with Permanent Marker used only for the marks. It works at phone width.

| Page | Content |
|---|---|
| **Runs** | Start a run (character, platforms, rounds, searches per round, cards wanted, time limit); recent runs with state |
| **Live run** | State, round, found / analyzed / filtered counts, explore ratio; the Master's reasoning per round with refused proposals; one tile per search agent with its latest action (live over SSE); activity feed; stop / resume |
| **Review** | One card at a time: contact sheet with the grease mark, rank, overall, 4 sub-score bars, adaptation idea, justification, motion window, feasibility notes, cross-check disagreement, fit breakdown, link to the original. Select / Pass / Decide later + note. A film strip shows every card and its mark. Once all cards are marked: satisfaction 1–10 + note → Send feedback → the Learner's retrospective, then **Write brief** for each select. Filtered videos are listed below |
| **Characters** | Canonical image, niche state, runs, version. Detail: taste profile (view, edit → new version), directions tried with their select rate, example videos (add URL), the brief the agents receive |
| **Briefs** | Title, concept, what to record, framing, motion window, Kling prompt (copy button), shots, risks; copy the whole brief as markdown |
| **Settings** | Scoring weight sliders (must total 100) + the Learner's suggestion (fills the sliders; you save it); subscription status; platform health |

Keyboard on Review: `J`/`→` next, `K`/`←` previous, `U` select, `D` pass, `S` later, `N` note, `Esc` leaves the note.

Tests: `npx vitest run` (API client, review reducer). Browser smoke against a running app: `npm run smoke`. Live end to end through the UI (spends subscription usage): `node e2e/live.mjs`.
