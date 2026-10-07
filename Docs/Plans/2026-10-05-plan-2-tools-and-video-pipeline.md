# Plan 2: Tool Layer & Video Pipeline: Implementation Plan

> **For agentic workers:** executed inline with superpowers:executing-plans (TDD per task). Steps use checkbox syntax.

**Goal:** Agents can discover TikTok, Instagram and YouTube Shorts videos without any account, and any video URL becomes structured evidence: metadata, contact sheet, transcript, pose-based Kling feasibility, best clean segment and fingerprint, persisted in Postgres.

**Architecture:** `tf_agent.tools` (normalization, rate limiting, circuit breakers, DB cache, SearXNG + yt-dlp backed platform tools exposed as `loop.tools.Tool`) and `tf_agent.pipeline` (ffmpeg/OpenCV/MediaPipe/faster-whisper steps composed by `analyze_video`). New tables come in migration `0002`. SearXNG joins the Compose file.

**Tech Stack:** SearXNG (Docker), yt-dlp (Python API), httpx + trafilatura, ffmpeg/ffprobe, OpenCV (headless), MediaPipe Pose Landmarker, faster-whisper (CPU int8), Pillow, imagehash, numpy.

**Spec:** `Docs/Code docs/03-tools.md`, `04-video-pipeline.md`, `06-data-model.md`; decisions D-12…D-16, D-26, D-34.

**Plan format ruling:** The owner waived plan review and asked for all plans to be executed end to end, so Plans 2–4 are written as precise task specs (files, interfaces, behaviors, test list). They are not full-code transcripts. The code is written test-first directly in the repo. This halves token cost; the TDD gate per task is unchanged.

## Probe findings (2026-10-05, no accounts)

| Path | Result |
|---|---|
| SearXNG `site:tiktok.com …`, `site:instagram.com/reel …`, `site:youtube.com/shorts …` | 20 results each (Google CSE engine; Brave/DDG rate-limited) |
| yt-dlp metadata, TikTok video URL | works: views, likes, comments, reposts, saves, duration, timestamp, 1080×1920, track |
| yt-dlp metadata, Shorts URL | works: views, likes, comments, follower count, duration, timestamp |
| yt-dlp `ytsearchN:` | works (flat: id, duration, views) |
| yt-dlp / plain HTTP, Instagram reel | **login wall**: no metadata, no media |

→ Instagram in anonymous mode = **discovery only** (URL + search-snippet caption, metrics `null`, `media_access="login_required"`). Full support arrives with a logged-in session (Q-03), with no code change to callers.

## Global Constraints

- All Plan 1 constraints still hold. Run from the app root. Postgres on 127.0.0.1:5433; SearXNG on 127.0.0.1:8888.
- Tools never raise into agents: failures become `{"error": {"code": …, "message": …, "retry_after_s": …}}` with codes `rate_limited | login_required | platform_unavailable | not_found | invalid_input`.
- Canonical IDs: `tiktok:<id>`, `instagram:<shortcode>`, `youtube:<id>`. Hashtags lowercase without `#`; handles lowercase without `@`.
- Metrics that are unknown are `null`, never guessed.
- Downloads ≤ 720p mp4 into `media/videos/`. Contact sheets go to `media/sheets/` (kept). Analysis frames are temporary.
- CPU-only: at most 2 concurrent heavy pipeline jobs; at most 2 concurrent yt-dlp calls.
- `web_fetch`: HTTPS only, private/loopback/link-local destinations rejected, 5 MB cap.
- Audio fingerprint (Chromaprint) is **not** used: `fpcalc` isn't installable without sudo. Clustering uses sound ID + frame perceptual hashes (ruling, D-19 still satisfied).

## Review Focus

1. A SearXNG engine CAPTCHA or rate limit returns 0 results → the tool must report `platform_unavailable`/degraded, not an empty "no trends" success.
2. yt-dlp hangs on a slow platform → per-call timeout; the worker thread must not pin the pool forever.
3. The same video reached through different URL shapes (`/shorts/ID`, `watch?v=ID`, `youtu.be/ID`, TikTok short links, URLs with query strings) → one canonical ID.
4. `web_fetch` aimed at `http://localhost`, `https://127.0.0.1`, or a hostname resolving to `10.x` → refused.
5. A video with no audio, no person, or zero length → the pipeline returns a filtered analysis, never a crash.

---

### Task 1: Infra + migration 0002 (videos, video_analyses, tool_cache, platform_state)

**Files:** `Database/docker-compose.yml` (add `searxng`), `config/searxng/settings.yml`, `Database/tf_db/models/media.py`, `Database/tf_db/models/__init__.py`, `Database/migrations/versions/0002_media_tools.py`, test `Database/tests/test_media_tables.py`.

**Behavior:**
- `searxng` service: `searxng/searxng:latest`, port `127.0.0.1:8888:8080`, volume `../config/searxng:/etc/searxng`, env `SEARXNG_SECRET=${SEARXNG_SECRET:?set it in .env}`; settings enable `formats: [html, json]`, `limiter: false`, `image_proxy: false`.
- Tables (06-data-model.md): `videos` (canonical_id PK, platform, url, creator_handle, creator_followers, caption, hashtags text[], sound_id, sound_title, posted_at, duration_s, metrics jsonb, metrics_at, media_access, first_seen_at); `video_analyses` (id uuid7, canonical_id FK→videos, pipeline_version, probe/cuts/transcript/pose/best_clean_segment/fingerprint jsonb, cut_rate, camera_motion, feasibility, filtered_reason, contact_sheet_path, media_path, created_at; index on canonical_id); `tool_cache` (key PK, tool, response jsonb, expires_at; index expires_at); `platform_state` (platform PK, health, breaker_state, opened_at, failures, quota_used_today, updated_at).

**Tests:** the migration-vs-ORM test from Plan 1 now covers 0002 automatically; plus `test_media_tables.py`: video round-trip with a hashtags array; a video_analysis FK to a video; tool_cache expiry column.

### Task 2: Tool core: types, normalization, limiter, breaker, cache, platform health

**Files:** `Agent/tf_agent/tools/{__init__,types,normalize,limiter,breaker,cache,health}.py`; tests `Agent/tests/tools/test_normalize.py`, `test_limiter_breaker.py`, `test_cache_health.py`.

**Interfaces:**
- `types.VideoItem` (pydantic): canonical_id, platform, url, creator (handle, followers), caption, hashtags, sound (id, title, uses), posted_at, duration_s, metrics (views, likes, comments, shares, saves), thumbnail_url, media_access (`"ok"|"login_required"|"unknown"`), source (`"yt-dlp"|"searxng"|"api"`). Plus `ToolError(code, message, retry_after_s)` and `ToolFailure(Exception)` carrying a ToolError.
- `normalize.canonical_id(url) -> str | None` (TikTok `/@u/video/ID`, `vm.tiktok.com`/`vt.tiktok.com` → `None` until resolved; IG `/reel/X`, `/reels/X`, `/p/X`; YouTube `/shorts/ID`, `watch?v=ID`, `youtu.be/ID`), `platform_of(url)`, `norm_hashtag`, `norm_handle`, `norm_query`.
- `limiter.RateLimiter(min_interval_s, jitter_s=0, clock, sleep)`: `async acquire()` serializes and spaces calls.
- `breaker.CircuitBreaker(failure_threshold=5, window_s=300, open_s=600, clock)`: `allow() -> bool`, `record_success()`, `record_failure()`, `state` (`closed|open|half_open`).
- `cache.ToolCache(sessionmaker, clock)`: `async get(tool, key_input) -> dict | None`, `async put(tool, key_input, response, ttl_s)`. The key is sha256 of tool + normalized JSON input.
- `health.PlatformRegistry`: per platform → limiter + breaker + DB-persisted health (`ok|degraded|unavailable|needs_login`); `snapshot() -> dict[str, str]` for the Master's RunContext.

**Tests (min.):** every URL shape → expected canonical ID; junk URL → None; hashtag/handle normalization; limiter spacing with a fake clock; breaker opens after 5 failures within the window, refuses while open, goes half-open after `open_s`, closes on success; cache hit/miss/expiry against the DB; health snapshot reflects the breaker state.

### Task 3: Web tools: SearXNG search + safe page fetch

**Files:** `Agent/tf_agent/tools/web.py`; tests `Agent/tests/tools/test_web.py`.

**Interfaces:** `SearxClient(base_url, client_factory)`: `async search(query, max_results=10, time_range=None) -> SearchResponse(results=[SearchHit(title,url,snippet,engine,published)], unresponsive=[...])`, raising `ToolFailure(platform_unavailable)` when 0 results and every engine is unresponsive. `async web_fetch(url, *, resolver, client_factory) -> FetchedPage(title, text≤8000, links≤30)`.

**Tests:** JSON mapping via MockTransport; "0 results + all unresponsive" → platform_unavailable; "0 results, engines fine" → empty success; `web_fetch` rejects `http://`, `https://localhost`, IP literals in private ranges, and hostnames whose resolver returns 10.x/127.x/169.254.x/::1; enforces the 5 MB cap; extracts title/text/links from fixture HTML.

### Task 4: yt-dlp adapter

**Files:** `Agent/tf_agent/tools/ytdlp.py`, sample data (now made-up values in `Agent/tests/tools/samples.py`), tests `Agent/tests/tools/test_ytdlp.py`.

**Interfaces:** `info_to_video_item(info: dict) -> VideoItem` (pure); `YtDlp(timeout_s=45, max_parallel=2, cookies_file=None)`: `async metadata(url) -> VideoItem`, `async download(url, dest_dir, max_height=720) -> Path`, `async search_youtube(query, n) -> list[VideoItem]` (flat `ytsearchN`, keeps duration ≤ 180 s). Calls run in a thread with `asyncio.wait_for`; yt-dlp errors are classified (`login required|cookies` → login_required; `unavailable|private|removed|404` → not_found; `429|rate` → rate_limited; else platform_unavailable).

**Tests:** fixture mapping for TikTok and YouTube (metrics, posted_at from timestamp, sound from track, followers); error classification table; timeout path (a fake extractor that sleeps) → `ToolFailure(platform_unavailable)`; a `live` test that fetches metadata for one real Shorts URL.

### Task 5: Platform discovery tools + agent Tool wrappers

**Files:** `Agent/tf_agent/tools/platforms.py`, `Agent/tf_agent/tools/agent_tools.py`; tests `Agent/tests/tools/test_platforms.py`, `test_agent_tools.py`.

**Behavior:**
- `PlatformTools(searx, ytdlp, cache, registry, seen_filter=None)`:
  - `tiktok_search(query, max_results≤30)`, `instagram_search(query, max_results≤30)`, `shorts_search(query, max_results≤25)`, `tiktok_creator(handle)`, `tiktok_hashtag(tag)`, `get_video(url)`.
  - Flow: SearXNG `site:` query (TikTok: `site:tiktok.com`, plus `/@handle` and `/tag/x` variants; IG: `site:instagram.com/reel`; Shorts: `site:youtube.com/shorts` merged with yt-dlp `ytsearch`) → canonical IDs → dedupe → `seen_filter` (from Plan 3's blackboard; returns kept items + hidden count) → enrich via yt-dlp metadata (bounded parallel, cached 6 h) for TikTok/Shorts. Instagram items are built from the snippet (`media_access="login_required"`) unless a cookies file is configured.
  - Every call goes through the platform's breaker + limiter; results are cached; the response is `{"items": [...], "hidden_already_seen": n, "platform_health": "...", "notes": [...]}`.
- `agent_tools.build_tools(pt) -> list[Tool]` with pydantic params and **compact** outputs (≤ 12 items, one line each: id, views, age, duration, creator, caption ≤ 80 chars).

**Tests:** with fake Searx/YtDlp: dedupe across URL shapes; seen-filter count; IG items carry `login_required` and null metrics; breaker-open → `platform_unavailable` error payload (no exception); cache hit avoids a second yt-dlp call; compact output stays under the length budget; Tool param validation.

### Task 6: Pipeline A: probe, scenes, frames, contact sheet

**Files:** `Agent/tf_agent/pipeline/{__init__,ffmpeg,frames,sheet}.py`; tests `Agent/tests/pipeline/test_frames_sheet.py` (fixtures generated with ffmpeg lavfi at test time into tmp_path).

**Interfaces:** `async probe(path) -> Probe(duration_s, width, height, fps, has_audio)`; `async scene_cuts(path, threshold=0.3) -> list[float]`; `async sample_frames(path, out_dir, fps=2, width=360) -> list[Path]`; `async key_frames(path, out_dir, cuts, n=12) -> list[(t, Path)]`; `make_contact_sheet(frames_with_t, out_path, cols=3, rows=4, width=1024) -> Path` (timestamps drawn).

**Tests:** probe of a generated 3 s 720×1280 clip with a sine tone (has_audio True) and one without; a cut is detected in red→blue concatenation near 1.5 s; frame count ≈ fps×duration; contact sheet exists, 1024 px wide, 3×4 grid; zero-length/corrupt file → `PipelineError`.

### Task 7: Pipeline B: pose, camera motion, clean segments, feasibility

**Files:** `Agent/tf_agent/pipeline/{pose,motion,feasibility}.py`, test clips built at run time from the first character's main image (no stored copy), tests `Agent/tests/pipeline/test_pose_feasibility.py`.

**Interfaces:** `PoseAnalyzer(model_path)` (downloads MediaPipe `pose_landmarker_lite.task` to `~/.cache/trendfinder/models/` on first use) `.analyze(frames) -> PoseStats(per_frame_people, single_person_ratio, body_visibility, hands_visibility, face_size_ok, per_frame_single[bool])`; `camera_motion(frames) -> float` (0–1, OpenCV Farneback global flow); `clean_segments(t_per_frame, single_flags, cuts, motion_per_frame, min_len=3.0) -> list[(start,end)]`; `feasibility(stats, camera_motion, cut_rate, best_segment) -> (score 0-100, filtered_reason | None)`, implementing the formula and hard filter from 04-video-pipeline.md.

**Tests:** formula on hand-made stats (known value); hard-filter reasons; clean segments with cuts/multi-person frames; pose on a still-image video of one full-body person → single_person_ratio ≥ 0.8, body_visibility ≥ 0.6; two side-by-side people → mostly multi-person; a blank video → 0 people → filtered; motion of a static video ≈ 0, of a panning testsrc > static.

### Task 8: Pipeline C: transcript, fingerprint, retention

**Files:** `Agent/tf_agent/pipeline/{transcript,fingerprint,retention}.py`; tests `Agent/tests/pipeline/test_transcript_fp_retention.py`.

**Interfaces:** `Transcriber(model_size="small", compute_type="int8")` (lazy load, VAD on) `.transcribe(path) -> Transcript(language, text, speech_ratio, segments)`; returns an empty transcript without loading the model when there is no audio. `frame_hashes(frames) -> list[str]` (pHash hex); `hash_distance(a, b) -> float` (mean Hamming over aligned pairs, 0–64). `MediaRetention(media_dir, quota_bytes, protected: Callable[[], set[str]])`: `usage()`, `enforce()` deletes least-recently-used unprotected videos/frames until under quota; `can_download() -> bool` false at ≥ 95%.

**Tests:** no-audio path never constructs the model (monkeypatched factory asserts not called); tone-only audio → `speech_ratio` ≈ 0 with a fake model; hash distance 0 for identical frames, large for different; retention evicts oldest unprotected first, never protected, and `can_download` flips at 95%. A `live` test runs the real whisper model on a downloaded Shorts clip.

### Task 9: `analyze_video` orchestration, persistence, CLI

**Files:** `Agent/tf_agent/pipeline/analyze.py`, `Agent/tf_agent/tools/store.py` (upsert VideoItem → `videos`, save analysis → `video_analyses`), `Backend/tf_backend/cli.py` (`tf search`, `tf analyze`), `Backend/tf_backend/services.py` (tool + pipeline services); tests `Agent/tests/pipeline/test_analyze.py`, `Backend/tests/test_cli_tools.py`, live `Agent/tests/live/test_live_tools.py`.

**Behavior:** `VideoAnalyzer(ytdlp, transcriber, pose, retention, media_dir, max_parallel=2)` `.analyze(url_or_item) -> VideoAnalysis`: download (skipped for `login_required` → `filtered_reason="media_unavailable"`) → probe → cuts → frames → key frames → contact sheet → pose → motion → clean segments → transcript → hashes → feasibility → frames deleted → persisted. Heavy CPU steps run in a process pool (2 workers). Idempotent per `(canonical_id, pipeline_version)`: a second call returns the stored analysis.

**Tests:** full run on a generated person video (stub downloader copies a local file); idempotency; `login_required` path; corrupt download → filtered `probe_failed`. Live: `tf search tiktok "deadpan dance"` returns ≥ 5 items with view counts; `tf analyze <first TikTok url>` prints feasibility + contact sheet path; `tf search shorts …` works; `tf search instagram …` returns discovery-only items.
