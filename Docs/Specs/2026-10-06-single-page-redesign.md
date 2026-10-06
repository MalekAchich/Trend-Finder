# Single-page redesign: spec

**Status:** Approved in conversation (2026-10-06). Supersedes the multi-page UI of Plan 4, `04-character-profile-format.md` (profiles) and the production briefs (D-21).

## What the owner asked for

- **One page.** No sidebar, no menu, no tabs, no logo, no trace of the tool that generated the reference UI. The look is premium, modern and polished, in the wide, full-width direction of the reference UI export `agent-scroll-main` (Higgsfield-like), not a dashboard.
- **One fixed hybrid theme.** A dark top zone (inputs and the live run engine) and a light bottom zone (found videos), joined by a gradient seam. The seam animates only while a run is going. When a video is saved, a light "transfer" travels from the dark run area through the seam into the library, then the new card appears.
- **A character is only its images.** These live in `AI Influencers Characters/<Name>/`. There is no profile, bio or persona text anywhere. The agents read the images and discover the vibe, niche, context and target videos.
- **The page is centred on the run engine.**
  - Pick one character and start a run.
  - Watch the run logs and a live stream of every agent's actions and thinking, so the owner can investigate mid-run.
- **Two optional inputs:**
  - URLs of trending, high-view AI-influencer videos in general, used as trend references.
  - Target videos the owner found: rows of URL + the character each one is for.
- **Found videos are saved under character → run → videos.**
  - Each card shows a thumbnail and plays a muted platform-embed preview on hover, like YouTube.
  - Each card has 👍/👎 plus an optional note.
  - Each run has an optional satisfaction score (1–10) plus a note.
- **Only URL, metadata and one small thumbnail are kept per video.** No video files are stored.
- **Removed:** Briefs, Settings, the separate Characters / Runs / Review pages, and all code that only served them.

## 1. Characters

- **Discovery.**
  - Every sub-folder of `CHARACTERS_DIR` that contains at least one image (`.png`, `.jpg`, `.jpeg`, `.webp`) is a character.
  - Slug = lower-cased folder name with spaces turned to `-`. Name = folder name.
  - Non-image files are ignored. The old `Nicolaiz/profile.md` is deleted.
- **Main image.**
  - The image whose stem equals the folder name (`Tekashi67/Tekashi67.png`).
  - Otherwise, the first image alphabetically whose name doesn't contain "face" or "body".
  - Otherwise, the first image.
- **Versioning.**
  - Content hash = sha256 over the sorted (file name, file hash) pairs.
  - A new `character_versions` row is created only when the hash changes. It stores `images` (JSON list of absolute paths, main first) and `canonical_image_path`.
- **Sync** runs automatically: on `GET /api/characters`, at server start, and before every run. No sync button and no CLI step.
- **Character read.** This is the first phase of every run; run state `reading_character`.
  - A vision role, `reader`, sees up to 4 of the character's images, the latest taste profile (if any) and the owner's notes.
  - It returns `CharacterRead`:
    - `look`: what is fixed and visible: face, hair, outfit, era, style.
    - `vibe`: the attitude and energy the images project.
    - `performance_angle`: how this character would be funny or compelling on camera.
    - `possible_niches`: 3–6 short niche hypotheses.
    - `kling_constraints`: what Motion Control can and can't do with this look.
    - `avoid`
  - The read is stored on the run (`runs.character_read`) and streamed as one highlighted event.
  - The text "brief" that every other role receives is rendered from the read plus the taste profile. It replaces `build_brief(profile)`.
  - If the read fails on both providers, the run moves to `paused_usage` (limits) or `interrupted` (other errors) like any other phase.
- **Analyst scoring dimensions are generic, not tied to one character.** `fit_breakdown` = {look, vibe, energy, niche, adaptability}, each 0–10, and fit = mean × 10.

## 2. Run inputs

`POST /api/runs` takes:

```json
{ "character": "nicolaiz", "platforms": ["tiktok","instagram","youtube"],
  "freshness": "week", "minutes": 60,
  "trend_urls": ["https://…"], "targets": [{"url": "https://…", "character": "tekashi67"}] }
```

- **`freshness`** (`day` | `week` | `month` | `any`, default `week`) is passed as the `recent` filter to search tools.
- **`minutes`** is the wall-clock limit: default 60, chosen in an options popover.
- The rest of `RunSettings` keeps its defaults: 3 rounds, 12 tasks per round, target 20, 8 workers.
- **Validation.** Every URL must be a TikTok, Instagram or YouTube video URL that `canonical_id()` resolves (422 otherwise, naming the bad URL). A target's character must exist.
- **Trend URLs** are studied right after the character read:
  - Each one is downloaded and analysed temporarily (contact sheet + transcript), then the `seed_study` role describes its format, hook, why it works and search angles.
  - The studies are streamed and added to the Master's context ("formats trending in AI-influencer content right now").
  - The temporary files are deleted afterwards.
- **Targets for the run's character** enter as candidates directly (`source="owner"`). They skip the scouts, go through the normal analysis, and show up as found videos when they pass the feasibility filter.
- **Targets for another character** are saved as pending targets for that character and are consumed by its next run.
- Both kinds are persisted in the `targets` table (character, url, canonical_id, status `pending` | `used`, run_id).

## 3. Live stream

Every event is a row in `events`, streamed over SSE with `Last-Event-ID` replay as today. Payloads always carry `agent`: `{id, role, platform}`. The agent id is the task id, or `lead`, `reader`, or `analyst-<n>`.

| type | payload (besides `agent`) | emitted when |
|---|---|---|
| `run.state` | `state`, `stop_reason?` | state changes. New states: `reading_character`, `studying_trends` |
| `character.read` | the `CharacterRead` | after the read |
| `trend.studied` | `url`, `study` | per trend URL |
| `plan.created` | `round`, `reasoning`, `tasks[{task_id, role, platform, goal}]`, `refused[{goal, reason}]` | the Master planned a round |
| `agent.started` / `agent.finished` | `goal` / `accepted`, `rejected`, `leads`, `failed?` | worker lifecycle |
| `agent.thought` | `text` (≤ 1,200 chars) | every model step. Claude: the `thought` field. ChatGPT: the reasoning summary, plus any assistant text |
| `agent.tool_call` | `tool`, `args` | before each tool runs |
| `agent.tool_result` | `tool`, `summary` (≤ 300 chars), `ok` | after each tool |
| `candidate.rejected` | `canonical_id?`, `reason` | hallucinated ID, already seen, feasibility filter, analyst error |
| `analysis.started` / `analysis.finished` | `canonical_id`, `verdict` (`saved` \| `filtered` \| `failed`), `reason?` | per candidate |
| `video.saved` | a full `FoundVideo` | a scored finding passed the filter. This drives the transfer animation |
| `provider.switched` | `from`, `to`, `reason` | the ModelClient fell back to the other provider |
| `round.finished` | the round summary | as today |
| `error` | `message` | a task failed, both providers limited, etc. |

- **Workers' system prompts** require one or two plain sentences of reasoning before each tool call, so both providers produce a `thought`.
- **ChatGPT requests** set `reasoning.summary = "auto"`. The adapter collects `response.reasoning_summary_text.delta` into a new `CompletionResponse.reasoning` field.

## 4. Found videos and media

- **What a found video is.** An analysed, scored finding that passed the feasibility filter, after curation's de-duplication (one card per cluster). Video and score come from the cluster's best source.
- **During the run**, `video.saved` shows cards as they're scored. **At the end**, curation re-ranks and de-duplicates, and the library shows the curated set.
- **Thumbnail.** At analysis time, one frame from the middle of the best clean segment (or the middle of the video) is saved to `MEDIA_DIR/thumbs/<platform>_<id>.jpg` as a 360 px wide JPEG under 40 KB. It's recorded in `videos.thumbnail_path`.
- **Cleanup.**
  - The downloaded video, its frames and its audio are deleted right after analysis.
  - Contact sheets are kept until the run's curation finishes (cross-check needs them), then deleted.
  - `VideoAnalysis.media_path` / `contact_sheet_path` are cleared.
  - The old quota-based retention is removed.
- **Hover preview** uses the platform's embed player in an iframe after a 400 ms hover:
  - YouTube: `https://www.youtube.com/embed/{id}?autoplay=1&mute=1&controls=0&loop=1&playlist={id}`.
  - TikTok: `https://www.tiktok.com/player/v1/{id}?autoplay=1&muted=1&controls=0&loop=1`. Verified in a real browser before relying on it.
  - Instagram: thumbnail only, with click opening the post.
- **Detail sheet** (click): the embed, the 4 scores ("Fits the character", "Easy to recreate", "Growing fast", "Still fresh"), why it fits, the adaptation idea, the best segment to copy, what to watch out for, a link to the original, and 👍/👎 + note.

## 5. Feedback and learning

- **`PUT /api/videos/{cluster_id}/feedback`** `{rating: "up" | "down" | null, note: string | null}` saves immediately (null removes the rating). It's allowed in any run state.
- **`PUT /api/runs/{id}/feedback`** `{satisfaction: 1–10, note}` is allowed once the run has ended.
- **Learning is incremental and idempotent:**
  - Each rating change recomputes that run's direction contributions (stored per run and replaced, as today) and marks the cluster's videos as rated in `seen_items`.
  - The LLM taste-profile rewrite runs at the start of the character's next run, before the character read, when ratings or notes changed since the last taste version. It's streamed as `agent.thought` from `learner`.
  - Owner notes stay verbatim.
- **Satisfaction** still feeds the explore ratio (D-18).
- **Fixed in code:** scoring weights stay `DEFAULT_WEIGHTS`. The weight suggestion and the weights setting are removed.

## 6. API (all under `/api`; mutations need `x-trendfinder-client`; host check kept)

| route | purpose |
|---|---|
| `GET /health` | liveness |
| `GET /characters` | `[{slug, name, image_url, images[], runs}]` (auto-sync) |
| `GET /characters/{slug}/runs` | `[{id, state, started_at, finished_at, videos, satisfaction}]`, newest first |
| `POST /runs` | start (body above) → `{run_id}` |
| `GET /runs/active` | the active run or `null` (the page reconnects to it on load) |
| `GET /runs/{id}` | state, character, inputs, agents roster (derived from tasks), counts, error |
| `POST /runs/{id}/stop` · `POST /runs/{id}/resume` | as today |
| `GET /runs/{id}/events` | SSE |
| `GET /runs/{id}/videos` | `FoundVideo[]` (live set while running, curated set after) |
| `PUT /videos/{cluster_id}/feedback` · `PUT /runs/{id}/feedback` | feedback |
| `GET /providers` | kept for `tf doctor` and a small status indicator |
| `GET /media/{thumbs\|characters}/{path}` | confined media; video files are no longer served |

Removed: briefs, settings, platforms, taste-profile PUT, seeds POST, characters sync POST, `trends?include=filtered`, and the batch `POST /runs/{id}/feedback`.

## 7. Web app (`Frontend/`)

- **Stack:** Vite + React + TypeScript + Tailwind 4 + TanStack Query. No router and no other pages. Unknown paths are served `index.html` and render the one page.
- **Tokens.** Taken from the reference UI, then cleaned:
  - Dark zone: background `oklch(0.177 0.003 248)`, card `oklch(0.267 0.006 258)`, muted text `oklch(0.658 0.007 258)`.
  - Accent: lime `oklch(0.938 0.203 119)`.
  - Light zone: a warm off-white paper with ink text, using the same accent.
  - Fonts: DM Sans (UI) and Space Grotesk (headings).
- **Top (dark):** a quiet toolbar (no logo or brand mark; a provider-status dot on the right), the character picker (portrait tiles), the two optional input sections, platform toggles and freshness, an options popover (time limit), and Start / Stop.
- **Engine (dark).** Visible while a run is active or after one has finished in this session; collapsible.
  - **Header:** character, state, elapsed time.
  - **Left:** the agent roster: role, platform, status dot, current action. Click an agent to filter.
  - **Right:** the stream:
    - Each event type has a distinct treatment. Tool args and results are expandable JSON, and long text collapses.
    - Follow-live toggle, event-type filter, search.
    - Timestamps are relative.
  - **Above the stream:** a strip of the videos saved so far.
- **Seam:** gradient from dark to light.
  - **Idle:** static.
  - **Running:** a slow light drift.
  - **On `video.saved`:** a light pulse travels from the engine down through the seam, then the card enters the library grid.
  - `prefers-reduced-motion`: no motion; cards simply appear.
- **Library (light):**
  - Character switcher (avatars), then that character's runs (newest first, collapsible, with date, state and number of videos), then a 9:16 card grid.
  - Cards have hover embed preview, 👍/👎 and "Add note".
  - Clicking a card opens the detail sheet.
  - The end of each run has the satisfaction slider + note.
- **States:** no characters ("add a folder with images to …"), no runs yet, a run that found nothing, provider limits (explained in the stream), backend unreachable.
- **No trace of the UI-generator tool anywhere:** no text, assets, icons, meta tags or dependencies.

## 8. Cleanup

| Area | Remove |
|---|---|
| Frontend | All Plan 4 pages, components, the review reducer and their tests. `react-router-dom` if unused. The old e2e scripts are replaced |
| Backend API | `briefs`, `settings`, `platforms`, characters sync/taste/seeds routes, batch feedback |
| Agent | `characters/profile.py`, `characters/brief.py` (replaced by `characters/read.py`), `learning/briefs.py`, `learning/weights.py`, `prompts/brief.md`, `pipeline/retention.py` quota logic, the analyst's deadpan dimension |
| DB (migration 0005) | Drop `briefs`. Drop `character_versions.profile_md` / `front_matter`; add `images`. Replace `seeds` with `targets`. Add `videos.thumbnail_path` and `runs.character_read`. Drop setting keys `weights` / `weight_suggestion` |
| CLI | `sync-characters`, `seed add`. `run` gains `--trend-url` / `--target` |
| Docs | Rewrite 04 (character folder = images), 07, README quick start, decisions D-38…, Plans index |

## 9. Done means

- A live run started from the page shows the character read, plans, thoughts, tool calls and rejections as they happen.
- Saved videos fly into the library.
- Hover previews play for YouTube and TikTok.
- A rating with a note persists and reaches the next run's taste profile.
- Trend URLs and target URLs are used as described.
- pytest + vitest are green.
- A Playwright check runs on desktop and laptop widths, with no console errors.
- A case-insensitive search for the generator tool's name over `Frontend Backend Agent Database Docs` returns nothing.
