# Code/07: API & Frontend

**Status:** Draft v1, awaiting review

## REST API (FastAPI, `/api`)

| Method & path | Purpose |
|---|---|
| `GET /characters` · `GET /characters/{id}` | List / detail (current version, images, seeds, latest taste profile, direction stats) |
| `POST /characters/sync` | Re-scan `CHARACTERS_DIR`; new or changed `profile.md` → new character version |
| `GET /characters/{id}/taste-profile` · `PUT …` | Read / owner edit (creates a new version) |
| `POST /characters/{id}/seeds` | Add seed URLs; triggers download + pipeline |
| `POST /runs` | Start a run `{character_id, platforms, target_findings, caps?, explore_ratio_override?}` |
| `GET /runs` · `GET /runs/{id}` | List / detail (state, rounds, plans, tasks, stats) |
| `POST /runs/{id}/stop` · `POST /runs/{id}/resume` | Stop (finish accepted work, no new tasks) / resume a paused run |
| `GET /runs/{id}/events` | **SSE** stream; supports `Last-Event-ID` replay from the `events` table |
| `GET /runs/{id}/tasks/{task_id}` | Task drill-down: scope, steps, tool calls, result, transcript |
| `GET /runs/{id}/trends` | Ranked trend cards (`?include=filtered` for feasibility-filtered findings) |
| `GET /trends/{id}` | Card detail with all member findings |
| `POST /runs/{id}/feedback` | `{cards: [{cluster_id, rating, note?}], satisfaction, note?}`; triggers the Learner |
| `POST /trends/{id}/brief` · `GET /briefs/{id}` | Generate / read a production brief (only allowed for 👍 cards) |
| `GET /providers` | Provider health, cooling times, available models, calls/tokens today |
| `GET /platforms` | Platform health, login status, quota used |
| `GET /settings` · `PUT /settings` | Weights, thresholds, caps, role overrides; apply the Learner's weight suggestion |
| `GET /media/{path}` | Serves contact sheets and videos from `media/` (path-validated) |

## SSE event types

`run.state` · `round.started` · `plan.created` (with reasoning summary and rejections) · `task.queued` · `task.started` · `task.progress` (step, tool, short summary) · `task.finished` · `candidate.found` · `analysis.done` · `cluster.updated` · `provider.state` · `platform.state` · `run.review_ready` · `learner.done`

## Frontend (Vite + React + TypeScript + Tailwind + shadcn/ui + TanStack Query)

| Page | Content |
|---|---|
| **Characters** | Cards per character: canonical image, version, stats (runs, 👍 rate). Detail: profile (read-only, edited in the folder), seeds (add URL), **taste profile** (view, edit, version history), direction leaderboard (alpha/beta, last used) |
| **New Run** | Character, platforms, target findings, caps (advanced), explore ratio (pre-filled from the last satisfaction, overridable), provider/platform health warnings |
| **Live Run** | Round timeline with the Master's reasoning summary and plan; **worker grid** (one tile per task: type, platform, direction, provider/model, step count, latest action); candidates feed; usage per provider; stop button |
| **Review** | Ranked **trend cards**: embedded video + contact sheet, overall + 4 sub-score bars with one-line justifications, adaptation idea, best clean segment, source label, disagreement flag. Buttons 👍 / 👎 / skip + note field. Filtered tab. At the bottom: overall satisfaction 1–10 + note → Submit |
| **Briefs** | Briefs for 👍 cards: shot plan, Kling settings, prompt, framing notes, motion-reference window, what to record; copy buttons for each prompt |
| **Settings** | Weights (+ Learner suggestion with Apply), feasibility thresholds, caps, role → model mapping, provider status, platform logins status |

Review is keyboard-friendly (`J/K` navigate, `U` 👍, `D` 👎, `S` skip, `N` note) so rating 20 cards takes about a minute.
