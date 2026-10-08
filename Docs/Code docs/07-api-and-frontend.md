# Code/07: API & Frontend

**Status:** Built (Plans 5–6). Spec: `Docs/Specs/2026-10-06-single-page-redesign.md` + Plan 6 (navbar, pages, settings).

`tf serve` runs the API and the built web app on `http://127.0.0.1:8000`. It binds loopback only unless you pass `--allow-remote`. Runs execute inside the same process as background tasks. Unfinished runs resume on startup.

Guards:
- Every mutating route (POST/PUT) requires the `x-trendfinder-client` header, which blocks cross-site form posts.
- Requests must name the host `127.0.0.1`, `localhost` or `[::1]`, which blocks DNS rebinding.

## REST API (`/api`)

| Method & path | Purpose |
|---|---|
| `GET /health` | Liveness |
| `GET /characters` | `[{slug, name, image_url, images[], runs}]`. Syncs the image folders first |
| `GET /characters/{slug}` | Name, images, the latest character read, the latest taste profile (owner notes included), runs, videos found |
| `GET /characters/{slug}/videos?days=` | Found videos across all the character's runs, newest found first (`found_at`), optionally only the last N days |
| `GET /characters/{slug}/runs` | `[{id, state, stop_reason, started_at, finished_at, videos, satisfaction, active}]`, newest first |
| `POST /runs` | `{character, platforms[], freshness: day\|week\|month\|any, minutes, trend_urls[], targets[{url, character}]}` → `{run_id}`. 422 names a bad URL or an unknown character |
| `GET /runs?character=&limit=` | Run history: character, state, stop reason, started, finished, videos, score, tokens |
| `GET /runs/active` | The run that's going, or `null` (the page reconnects to it on load) |
| `GET /runs/{id}` | State, character, inputs, character read, round, time limit, the agents roster derived from tasks, finding counts |
| `POST /runs/{id}/stop` | Stop and rank what was found (curates in the background, ends `review_ready`; a second stop → 409) |
| `POST /runs/{id}/resume` | Resume a run that isn't active (409 for finished runs) |
| `GET /runs/{id}/events` | **SSE**; resumes from `Last-Event-ID` (or `?after=`), heartbeat every 15 s, closes after the run ends |
| `GET /runs/{id}/videos` | `FoundVideo[]`: the live set (analysed findings, `cluster_id: null`) while running, the curated set after |
| `PUT /videos/{cluster_id}/feedback` | `{rating: up\|down\|null, note}`. Saves immediately; `null` clears it |
| `PUT /runs/{id}/feedback` | `{satisfaction: 1–10, note}`. Finished runs only (409 otherwise) |
| `GET /providers` | Subscription status (connected, ok/cooling/auth_error) for the navbar |
| `GET /usage` | Per subscription: status, % of the usage window used, window length and reset (ChatGPT reports these; Claude doesn't), cooling-until, last error, tokens and calls in the last 24 h, average tokens per run (last 20 runs) |
| `GET /settings/models` · `PUT /settings/models` | Per provider: offered models, the chosen main + fast model and effort, the efforts allowed. PUT `{provider, main, fast, effort|null}` validates and applies immediately (stored in `settings.models`, loaded at start) |
| `GET /settings/priority` · `PUT /settings/priority` | `{first, providers}` (`first` is a provider or null). PUT `{first}` sets which subscription every agent tries first (null = Balanced); 422 for an unknown provider. Stored in `settings.provider_priority`, loaded at start (D-47) |
| `GET /manual-videos?character=` · `POST /manual-videos` | The owner's manually chosen videos (D-51), as cards: status (checking/ready/problem), creator, views/likes, length, roles (`is_reference`, `target`) and the study for the character. POST `{urls (≤ 100), reference, target}` saves them and checks each in the background |
| `PATCH /manual-videos/{id}` · `POST /manual-videos/{id}/check` · `DELETE /manual-videos/{id}` | Change roles (`is_reference`, `target`; a video must keep one), look it up again (fresh, skipping the cache), remove it |
| `GET /download/{canonical_id}?kind=video\|audio` | The owner's copy of a found or manually chosen video (D-53): the video with its sound as an H.264 mp4 up to 1080p, or the sound alone as an mp3, sent as an attachment. Only videos the app knows; 502 with the reason when the platform refuses |
| `POST /characters` (form: `name`, `images`) · `PATCH /characters/{slug}` `{name}` · `POST /characters/{slug}/images` (form: `images`) | Add a character (a new folder; the first image becomes the main one, named like the folder), rename one (the folder and its main image are renamed, and its runs, ratings and targets stay with it; not while its run is going), or add images (never overwriting). PNG / JPEG / WebP, ≤ 15 MB, ≤ 12 at a time. A folder renamed by hand keeps its character too (same images) |
| `GET /settings/accounts` | Accounts & keys: per item `{id, label, kind: key\|session, set, hint, updated_at, status, connecting}`; never a secret (D-49) |
| `PUT /settings/accounts/{id}` · `DELETE /settings/accounts/{id}` | PUT `{value}` saves a key after checking it with the provider (422 if rejected, value never echoed); DELETE removes a key or disconnects an account |
| `POST /settings/accounts/{platform}/connect` | 202: opens a visible login window for `tiktok`, `instagram` or `x`; progress in `connecting` (409 if one is already open) |
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

## Web app (`Frontend/`: Vite + React 19 + TypeScript + Tailwind 4 + TanStack Query + React Router)

There's a small top navbar (52 px):
- the animated logo (the intro plays once when the app opens; it loops while a run is active);
- links: Home, Characters, Runs, Socials, Settings;
- on the right, the run status and both subscriptions' status.

The fixed hybrid theme is kept on every page: dark on top, the seam, and a grey-ish library below.

| Page | Content |
|---|---|
| **Home** | Composer ("What should *Name* post next?", whole-image character cards, both optional inputs openable together with an animated reveal, platforms, freshness, time limit, Start / Stop). The **Engine** (roster, live stream, filters, saved strip). The **seam**, whose lights drift while running and which a yellow pulse sweeps top to bottom when a video is saved. **Found videos** by character and run, with hover previews, 👍/👎 + note, Video / Sound downloads, and the run score (with its note) |
| **Characters** | Character switcher, the images, "What the agents see" (latest read), "What they learned from your ratings" (taste profile), runs and videos found, then the character's found videos filtered by found date (Today, 7 days, 30 days, All) |
| **Runs** | History table (filter by character). `/runs/:id` replays the run's stream (agents end as done or failed), with its videos and score |
| **Settings** | Usage per subscription (ChatGPT window % and reset; Claude status), tokens in the last 24 h, average per run. Models per provider: main, fast, effort, Save |
| **Socials** | Empty for now |

Tests:
- `npx vitest run`: the stream reducer, SSE parsing, embed URLs, hover intent, the logo cycle and the API client.
- `npm run smoke`: a browser check against a running app.
- `node e2e/live.mjs`: a live run through the page (spends subscription usage).
