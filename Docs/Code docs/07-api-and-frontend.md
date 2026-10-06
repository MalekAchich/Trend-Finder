# Code/07: API & Frontend

**Status:** Built (Plan 5, single page). Spec: `Docs/Specs/2026-10-06-single-page-redesign.md`.

`tf serve` runs the API and the built web app on `http://127.0.0.1:8000`. It binds loopback only unless you pass `--allow-remote`. Runs execute inside the same process as background tasks. Unfinished runs resume on startup.

Guards:
- Every mutating route (POST/PUT) requires the `x-trendfinder-client` header, which blocks cross-site form posts.
- Requests must name the host `127.0.0.1`, `localhost` or `[::1]`, which blocks DNS rebinding.

## REST API (`/api`)

| Method & path | Purpose |
|---|---|
| `GET /health` | Liveness |
| `GET /characters` | `[{slug, name, image_url, images[], runs}]`. Syncs the image folders first |
| `GET /characters/{slug}/runs` | `[{id, state, stop_reason, started_at, finished_at, videos, satisfaction, active}]`, newest first |
| `POST /runs` | `{character, platforms[], freshness: day\|week\|month\|any, minutes, trend_urls[], targets[{url, character}]}` → `{run_id}`. 422 names a bad URL or an unknown character |
| `GET /runs/active` | The run that's going, or `null` (the page reconnects to it on load) |
| `GET /runs/{id}` | State, character, inputs, character read, round, time limit, the agents roster derived from tasks, finding counts |
| `POST /runs/{id}/stop` | Stop and rank what was found (curates in the background, ends `review_ready`; a second stop → 409) |
| `POST /runs/{id}/resume` | Resume a run that isn't active (409 for finished runs) |
| `GET /runs/{id}/events` | **SSE**; resumes from `Last-Event-ID` (or `?after=`), heartbeat every 15 s, closes after the run ends |
| `GET /runs/{id}/videos` | `FoundVideo[]`: the live set (analysed findings, `cluster_id: null`) while running, the curated set after |
| `PUT /videos/{cluster_id}/feedback` | `{rating: up\|down\|null, note}`. Saves immediately; `null` clears it |
| `PUT /runs/{id}/feedback` | `{satisfaction: 1–10, note}`. Finished runs only (409 otherwise) |
| `GET /providers` | Subscription status (connected, ok/cooling/auth_error) for the toolbar |
| `GET /media/{thumbs\|characters}/{path}` | Video thumbnails and character images, confined to their roots (traversal → 404). Video files are never stored or served |

Any other non-`/api` GET serves the web app.

## Live events (SSE)

Every payload carries `agent: {id, role, platform}`. Roles: `lead`, `reader`, `learner`, `scout`, `radar`, `deep_dive`, `analyst`, `seed_study`, `owner`.

| Event | Payload |
|---|---|
| `run.state` | `state` (`created`, `reading_character`, `studying_trends`, `planning`, `running`, `paused_usage`, `curating`, `review_ready`, `interrupted`…), `stop_reason?` |
| `character.read` | `read` (look, vibe, performance_angle, possible_niches, kling_constraints, avoid) |
| `trend.studied` | `url`, `study` (format, hook, why_it_works, search_angles) |
| `plan.created` | `round`, `reasoning`, `tasks[{task_id, role, platform, goal}]`, `refused[{goal, reason}]` |
| `agent.started` / `agent.finished` | `goal` / `accepted`, `rejected`, `leads`, `failed?` |
| `agent.thought` | `text` (≤ 1,200 chars) |
| `agent.tool_call` / `agent.tool_result` | `tool`, `args` / `tool`, `summary` (≤ 300 chars), `ok` |
| `candidate.rejected` | `canonical_id?`, `reason` |
| `analysis.started` / `analysis.finished` | `canonical_id` / `verdict` (`saved`, `filtered`, `failed`), `reason?` |
| `video.saved` | `video` (a `FoundVideo`) |
| `provider.switched` | `from`, `to`, `reason` |
| `round.finished`, `error` | the round summary / `message` |

## Web app (`Frontend/`: Vite + React 19 + TypeScript + Tailwind 4 + TanStack Query)

One page and one fixed hybrid theme. There's no router, menu or logo.

1. **Toolbar**: what's running, and the status of both subscriptions.
2. **Composer** (dark):
   - "What should *Name* post next?"
   - Character portraits from the folder.
   - The two optional inputs: trending AI-influencer URLs as chips, and target URL rows tagged with a character.
   - Platform toggles, freshness, a time-limit dropdown, and Start / Stop.
3. **Engine** (dark):
   - Header: character, state, elapsed time.
   - Agent roster: role, platform, status, current action. Click an agent to filter.
   - Live stream, with distinct rows for each event type, expandable tool arguments, follow-live, group filters and search.
   - Strip of the videos saved so far.
4. **Seam**: the dark-to-light gradient. Lights drift while a run is going. On `video.saved` a light travels from the engine into the library, and the new card scales in. All motion is off under reduced motion.
5. **Found videos** (light):
   - Character switcher, then that character's runs (newest first), then a 9:16 grid of cards.
   - Each card has a thumbnail, platform and score badges, and why it fits.
   - Hovering 400 ms plays the platform's muted embed (YouTube, TikTok; Instagram shows the thumbnail).
   - Each card has 👍/👎 and a note. Clicking opens a detail sheet with the player, the 4 scores, the adaptation idea, the best segment to copy and the link.
   - Each finished run ends with a 1–10 score and a note.

Tests:
- `npx vitest run`: the stream reducer, embed URLs, hover intent and API client.
- `npm run smoke`: a browser check against a running app.
- `node e2e/live.mjs`: a live run through the page (spends subscription usage).
