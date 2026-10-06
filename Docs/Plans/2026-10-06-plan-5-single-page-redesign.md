# Plan 5: Single-page redesign: Implementation Plan

> **For agentic workers:** executed inline with superpowers:executing-plans (TDD per task). Task-spec format (owner waived plan review: "start and finish all plans", "go on").

**Goal:** Characters are image folders. The run reads the images, studies the owner's trend URLs, and takes the owner's target URLs. The owner watches every agent's thoughts and tool calls live on one page. Found videos are kept as URL + metadata + a small thumbnail, are previewable on hover, and are rated 👍/👎 with notes.

**Architecture:**
- Migration `0005` reshapes characters, targets, videos and runs, and drops briefs.
- `tf_agent.characters` becomes `folders.py` (images-only sync) + `read.py` (the `reader` vision role + brief rendering).
- The agent loop, the ModelClient and the orchestrator emit a richer event catalogue.
- The pipeline keeps only a thumbnail.
- The Learner becomes incremental.
- `tf_backend` exposes the slimmer API.
- `Frontend/` is rebuilt as one page in the reference UI's direction.

**Spec:** `Docs/Specs/2026-10-06-single-page-redesign.md` (binding). Earlier docs still apply where the spec is silent.

## Global Constraints

- All earlier constraints hold: subscription auth only, Postgres on 5433, never touch 5432, secrets never logged, loopback + host check, `x-trendfinder-client` on mutations.
- No profile, bio or persona text is required or shown anywhere. A character = image files in its folder.
- Per video, only URL, metadata, analysis numbers and one thumbnail of ≤ 40 KB at 360 px wide persist. Video files are deleted right after analysis. Contact sheets are deleted after the run's curation.
- Event payloads always carry `agent: {id, role, platform}`. Thought text is ≤ 1,200 chars and tool result summaries ≤ 300.
- Scoring weights are fixed at `DEFAULT_WEIGHTS`.
- Frontend: one page, no router, no sidebar/menu/tabs/logo. Fixed hybrid theme: dark top, light library, animated seam.
- No trace of the UI-generator tool (name, assets, icons, meta, dependencies) anywhere in the repo outside the owner's `agent-scroll-main/` folder, which is never committed.

## Review Focus

1. **Odd character folders:** spaces and uppercase in file names ("Tekashi67 face.png"), a folder without images, a stray `profile.md`. Expect the right main image and slug, the empty folder ignored, the `.md` ignored.
2. **A video re-found in a later run after its contact sheet was cleaned up.** Expect it to be re-analysed, never sent to the analyst with a missing file.
3. **Reloading the page mid-run.** Expect the stream to replay without duplicates and the agent roster to rebuild from events.
4. **Toggling a rating up → down → none quickly.** Expect the final state to persist and direction stats to count only the final rating.
5. **A target URL that is private, removed or login-walled.** Expect a `candidate.rejected` event that says why, and the run to carry on.

---

### Task 1: Migration 0005 + models

`Database/migrations/versions/0005_single_page.py`, models in `tf_db/models/{runs,media,feedback}.py`, tests in `Database/tests`.

- `character_versions`: drop `profile_md` and `front_matter`; add `images JSONB NOT NULL DEFAULT '[]'`. Existing rows get `images = [canonical_image_path]`.
- `seeds` is replaced by `targets`: `(id, character_id FK cascade, url, canonical_id, status pending|used, run_id FK set null, created_at)`, unique on `(character_id, canonical_id)`.
- `video_analyses`: add `thumbnail_path TEXT`. The thumbnail shares the analysis lifecycle. Ruling vs the spec's `videos.thumbnail_path`, recorded in the ledger.
- `runs`: add `character_read JSONB`, `inputs JSONB NOT NULL DEFAULT '{}'` and `feedback_contributions JSONB NOT NULL DEFAULT '{}'`.
- `run_feedback`: drop `contributions`.
- Drop the `briefs` table. Delete settings rows `weights` / `weight_suggestion`.
- `card_feedback.rating`: check constraint `up|down`. Existing `skip` rows are deleted.

**Tests:** ORM ↔ migration sync (existing test); `targets` uniqueness; the downgrade path runs.

### Task 2: Images-only characters + the reader role

- **Remove:** `characters/profile.py`, `brief.py`, `sync.py`, `prompts/brief.md`.
- **Create:**
  - `characters/folders.py`:
    - `discover(dir) -> list[FolderCharacter(slug, name, folder, images[main first])]`
    - `main_image(name, images)`
    - `sync_characters(dir, sm) -> list[SyncResult]`, a version bump on the image hash only.
    - `load_character(sm, slug) -> LoadedCharacter(character_id, version_id, slug, name, images, canonical_image_path)`
  - `characters/read.py`:
    - `CharacterRead` (pydantic per spec §1)
    - `render_brief(name, read, taste_md|None) -> str` (≤ 2,400 chars)
  - `prompts/reader.md`
- **Modify:**
  - `Roles`: add `read_character(images, taste_md, notes) -> Judged[CharacterRead]`, sending ≤ 4 images.
  - `config/roles.yaml`: `reader` (claude best, chatgpt best medium).
  - `AnalystResult.fit_breakdown` = {look, vibe, energy, niche, adaptability}, with `analyst.md` / `cross_check.md` / `master.md` / workers' prompts made character-agnostic. They say "the character described in the brief"; no "deadpan".
  - Delete `../AI Influencers Characters/Nicolaiz/profile.md`.
- **Tests:**
  - Discovery on a temp tree reproducing the real folders (spaces, case, `.md` ignored, empty folder ignored).
  - Main-image rule.
  - Version bumps only when an image changes.
  - `render_brief` stays within budget and includes the read + taste.
  - The reader request carries the images in order.
  - The analyst schema round-trips.

### Task 3: Media lifecycle: thumbnails, no stored videos

- **`VideoAnalyzer._analyze_media`:**
  - After the heavy step, write `thumbs/<safe>.jpg` via ffmpeg: frame at the middle of the best clean segment or of the video, `scale=360:-2`, quality stepped down until ≤ 40 KB.
  - Delete `videos/<safe>` in `finally`, always.
  - Store `thumbnail_path`, with `media_path=None`.
- **`analyze()`:** if a cached analysis isn't filtered but its contact sheet file is missing, re-analyse (Review Focus 2).
- **Remove** `MediaRetention` and the quota wiring.
- **Add** `cleanup_run_media(sm, run_id)`: delete the run's contact sheets after curation and null `contact_sheet_path`. The orchestrator calls it after `_curate` in both `execute` and `stop_and_curate`.
- **Tests:**
  - Thumbnail created, ≤ 40 KB, 360 wide.
  - Video directory gone after success and after failure.
  - Missing sheet → re-analysis.
  - Cleanup deletes only that run's sheets.

### Task 4: Live event catalogue

- **Agent loop** (`loop/agent.py`):
  - `on_event` receives `AgentEvent(kind: thought|tool_call|tool_result|submitted|failed, step, text, tool?, args?, ok?)`.
  - Thought = `resp.reasoning` or `resp.text`.
  - Worker system prompts add the rule "before each tool call, write one or two sentences on why".
- **ChatGPT adapter:** request `reasoning.summary="auto"`; collect `response.reasoning_summary_text.delta` into `CompletionResponse.reasoning`.
- **ModelClient:** optional `on_switch(ctx, from_provider, to_provider, reason)` awaited when a later candidate is tried after an earlier one failed.
- **Orchestrator** emits the spec §3 catalogue:
  - `plan.created` gets its new shape.
  - New: `agent.started` / `agent.finished`, `agent.thought` / `agent.tool_call` / `agent.tool_result`, `candidate.rejected`, `analysis.started` / `analysis.finished`, `video.saved` (with a `FoundVideo` dict built by a shared `found_video(finding, score, video, analysis, cluster_id=None)` helper), `provider.switched`, `error`.
  - New run states: `reading_character`, `studying_trends`.
- **Tests:**
  - Loop event order for a scripted run.
  - Reasoning parsed from a recorded SSE fixture.
  - `on_switch` fires on fallback.
  - An orchestrator happy path yields every event type with an `agent` field.
  - `video.saved` payload shape.

### Task 5: Run inputs: character read, trend studies, targets

- **`RunSettings`** gains `freshness`, `trend_urls`, `targets`, stored in `runs.inputs`.
- **`Orchestrator.create_run(slug, settings)`:**
  - Validates URLs with `canonical_id` (`InputError` names the bad one).
  - Persists targets: the run's own character → `status=used, run_id`; others → `pending`.
- **`execute`** phases:
  1. `learner.refresh_taste` (Task 6)
  2. `reading_character` → `character.read`, stored in `runs.character_read`
  3. `studying_trends`: each trend URL goes through metadata (`platforms.get_video`) → analyzer → `study_seed`, giving `trend.studied`. Studies are added to the Master context.
  4. Own targets (this run + pending ones for this character) are fetched → `Video` upsert → finding `source=owner` → analysis queue. Failures give `candidate.rejected` with the reason (Review Focus 5).
  5. Rounds, as today. Workers pass `recent=freshness` to search tools.
- A resumed run skips phases already recorded.
- **CLI:** `tf run` gains `--trend-url` and `--target url[=slug]` (repeatable). Remove `sync-characters` and `seed add`.
- **Tests:**
  - Invalid URL rejected.
  - Targets for another character stay pending and are consumed by that character's next run.
  - Trend study context reaches the Master prompt.
  - A login-walled target emits a rejection while the run continues.
  - Resume doesn't redo the read.

### Task 6: Incremental feedback + learning; remove briefs and weights

- **`Learner`:**
  - `rate(cluster_id, rating: "up"|"down"|None, note) -> None`:
    - Upsert the rating, or delete it on None; bump `updated_at`.
    - Recompute the run's contributions and apply the delta to direction alpha/beta (stored in `runs.feedback_contributions`).
    - Mark members as `seen_items.rated`.
    - Serialised per run with `SELECT … FOR UPDATE` on the run row (Review Focus 4).
  - `rate_run(run_id, satisfaction, note)`, allowed once the run has ended.
  - `refresh_taste(character_id, run_id) -> int|None`: rewrites the taste profile when any rating or note changed since the latest version. Input: the last 60 rated cards across runs. Owner notes stay verbatim.
- **Remove:** `learning/briefs.py`, `learning/weights.py`, `Setting` reads in the orchestrator, the brief role in `roles.yaml`.
- **Tests:**
  - up → down → None leaves stats at zero for that card.
  - Concurrent rates on one run serialise.
  - `refresh_taste` skips when nothing changed and versions up when something did.
  - Owner notes kept verbatim.
  - `rate_run` is rejected while the run is running.

### Task 7: API surface + cleanup

- **Routes per spec §6:**
  - `api/characters.py` (list with auto-sync, `/{slug}/runs`)
  - `api/runs.py` (POST with the new body, `/active`, detail with the agents roster derived from tasks + `reader` / `lead`, stop/resume, SSE)
  - `api/videos.py` (`GET /runs/{id}/videos` with the live set while running and the curated set after; `PUT /videos/{cluster_id}/feedback`; `PUT /runs/{id}/feedback`)
  - `api/media.py` (kinds `thumbs` | `characters`)
- **Delete** `api/trends.py` and `api/settings.py`, plus the doctor/CLI references to removed things.
- **The live video set:** found videos from the run's analysed findings before curation, with `id = finding id` and `cluster_id = null`. Rating requires `cluster_id`, so cards become rateable after curation. While a run is going, cards show "rate after the run".
- **Tests:**
  - Each route's happy path and errors.
  - `/runs/active`.
  - Live → curated video sets.
  - Feedback idempotent.
  - Traversal still 404s.
  - Host check and header still enforced.

### Task 8: The single page (`Frontend/`)

- **Remove:** the old pages, components, review reducer, router dependency, and old e2e scripts.
- **Port the reference UI direction:** tokens, fonts, spacing. Nothing else from that export (no TanStack Start, no server layer, no generator code or assets).
- **Create:**
  - `src/api/{client,types}.ts`
  - `src/stream/{useRunStream.ts, reduce.ts}`: a pure reducer building the roster + filtered events. De-duplicated by id (Review Focus 3).
  - Components: `Composer`, `CharacterPicker`, `UrlChips`, `TargetRows`, `OptionsPopover`, `Engine`, `AgentRoster`, `StreamEvent`, `Seam`, `Library`, `VideoCard` (hover embed after 400 ms), `VideoSheet`, `RunFeedback`.
  - `src/embed.ts`: platform → embed URL.
- **Animation:**
  - The seam drifts while `running`.
  - `video.saved` triggers a transfer pulse: a positioned element animates from the stream's position to the library grid's insertion point via the Web Animations API, then the card mounts with a scale-in.
  - All of it is disabled under reduced motion.
- **Tests (vitest):**
  - The stream reducer: dedupe, roster status, filters.
  - The embed URL builder.
  - The API client.
  - Hover delay logic.
- `npm run build` passes.

### Task 9: Live end to end, docs, sweep

- **Browser checks:** Playwright smoke (`npm run smoke`) at 1440 and 1280 widths, no console errors. Verify the TikTok and YouTube embeds play muted on hover.
- **Live run from the page:** Nicolaiz with one trend URL and one target, watching events and the transfer animation; rate with a note; confirm `refresh_taste` runs at the next run start.
- **Docs:** rewrite `04` (character folder = images), `07`, README quick start, decisions D-38…D-42, Plans index.
- **Sweep:**
  - `ruff check`, plus a grep for imports of removed modules.
  - A case-insensitive search for the generator tool's name over `Frontend Backend Agent Database Docs` comes back empty.
