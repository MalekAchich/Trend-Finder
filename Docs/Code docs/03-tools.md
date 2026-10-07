# Code/03: Tool Layer (Search & Scraping)

**Status:** Draft v2. Anonymous mode first (D-34); logged-in sessions (Q-03) and the YouTube API key (Q-04) plug in later via config.

## Principles (D-12, D-13)

- Agents decide **what** to search; tools decide **how**. Agents only ever see normalized JSON.
- Each platform is isolated: its own rate limiter, circuit breaker, cache and error codes.
- All tools are **blackboard-aware**: already-seen videos are filtered out and counted (`01-agents.md`).
- Tool results returned to agents are **compact** (top N items, short fields). Full data is saved in the DB.

## Common types

```json
// VideoItem: every platform tool returns these
{
  "canonical_id": "tiktok:7412345678901234567",
  "platform": "tiktok",
  "url": "https://www.tiktok.com/@user/video/7412…",
  "creator": {"handle": "user", "followers": 120000},
  "caption": "…",
  "hashtags": ["deadpan", "dance"],
  "sound": {"id": "sound:7290…", "title": "…", "uses": 54000},
  "posted_at": "2026-10-02T18:00:00Z",
  "duration_s": 14.2,
  "metrics": {"views": 2100000, "likes": 310000, "comments": 2100, "shares": 18000, "saves": 40000},
  "thumbnail_url": "…"
}

// ToolError: never raise into the agent; return this instead
{"error": {"code": "rate_limited | login_required | platform_unavailable | not_found | invalid_input", "message": "…", "retry_after_s": 60}}
```

Missing metrics are `null`, never guessed.

## Tool catalog

| Tool | Input | Output | Implementation |
|---|---|---|---|
| `web_search` | `query, max_results≤10, time_range?` | `[{title, url, snippet, engine}]` | SearXNG JSON API (Docker) (D-14) |
| `web_fetch` | `url` | `{title, text (≤ 8k chars), links[≤ 30]}` | httpx + trafilatura; HTTPS only; private IPs blocked; 5 MB cap |
| `tiktok_search` | `query, max_results≤30, sort?` | `VideoItem[]` | Playwright, logged-in throwaway profile; reads the web app's JSON responses |
| `tiktok_hashtag` | `hashtag, max_results≤30` | `{hashtag_stats, items: VideoItem[]}` | Same |
| `tiktok_sound` | `sound_id or url, max_results≤30` | `{sound, uses, items: VideoItem[]}` | Same; key tool for following trends |
| `tiktok_creator` | `handle, max_results≤30` | `{creator, items: VideoItem[]}` | Same |
| `tiktok_trends` | `region, period(7d/30d), kind(hashtags/songs/videos)` | trend rows | Playwright on TikTok Creative Center (public) |
| `instagram_search` | `query or hashtag, max_results≤30` | `VideoItem[]` (reels only) | Playwright, logged-in throwaway profile; JSON responses |
| `instagram_audio` | `audio_id or url` | `{audio, items: VideoItem[]}` | Same |
| `instagram_creator` | `handle, max_results≤30` | `{creator, items: VideoItem[]}` | Same |
| `shorts_search` | `query, max_results≤25, published_after?, region?` | `VideoItem[]` | YouTube Data API v3 `search.list` (`videoDuration=short`) + `videos.list` for stats |
| `shorts_channel` | `channel_id or handle` | `{channel, items: VideoItem[]}` | YouTube Data API |
| `get_video` | `url` | `VideoItem` + `{media_path}` (download, ≤ 720p) | yt-dlp (cookies from the throwaway profiles for Instagram) |

`get_video` is usually triggered by the orchestrator (analysis), not by agents. Agents call it only when a task needs one specific URL resolved.

## Access modes per platform (D-34, D-48 to D-50)

Each search uses the best source it has, and falls back by itself (the result's `notes` say which one answered):

| Platform | 1. Connected account / key (Settings, Accounts & keys) | 2. Anonymous fallback |
|---|---|---|
| TikTok | The site's own search (`/search/video`), read from the JSON it fetches (`tools/session_search.py`) | SearXNG `site:tiktok.com/@` queries, then yt-dlp metadata |
| Instagram | Instagram's keyword search JSON; the session's cookies also unlock yt-dlp metadata and analysis | SearXNG `site:instagram.com/reel` (discovery only) |
| YouTube Shorts | YouTube Data API v3 `search.list` (`videoDuration=short`, `publishedAfter`) + `videos.list`, 101 units a search, daily quota tracked in `settings.youtube_quota` | yt-dlp `ytsearchN:` (≤ 180 s) plus SearXNG `site:youtube.com/shorts` |
| X | X's own search (`filter:native_video`, `since:`), read from the `SearchTimeline` JSON | SearXNG `site:x.com` (finds few, older posts) |

- **Fallback triggers:** a captcha or bot check, a login wall (the session is then marked expired in Settings), or a failure all fall back.
- **TikTok trends:** TikTok Creative Center moved behind a TikTok One login in 2026. The trends tool gets built once a TikTok account is connected and its real data can be read.
- **TikTok Research API:** not used. It's academic-only, limited to certain regions, and excludes commercial use.

## Accounts and keys

- **Storage:** everything lives in `secrets/` (`tf_agent/credentials.py`): `keys.json` and `sessions/<platform>/{state.json, cookies.txt, meta.json}`. Files are 0600 and folders 0700.
- **The API never returns a secret,** only whether it's set, a masked hint and dates.
- **Connecting an account** (Settings, or `POST /api/settings/accounts/{platform}/connect`):
  - It opens a **visible** Chromium window on this computer. The owner logs in by hand (2FA and checkpoints included).
  - Only the session is saved, never the password.
- **The YouTube key** is checked with one 1-unit call before it's saved.
- **Credentials are read at use time,** so rotating one needs no restart.
- **Never** use the character's real posting accounts here.

## Rate limiting and politeness

| Platform | Default limit | Notes |
|---|---|---|
| TikTok (Playwright) | 1 page action per 3–6 s (jittered), max 2 parallel contexts | Human-like scrolling; stop on captcha → `platform_unavailable` |
| Instagram (Playwright) | 1 action per 5–10 s, 1 context | Stricter anti-bot; lowest volume |
| YouTube Data API | Quota tracker: 10,000 units/day; `search.list` = 100 units | Rejects with `rate_limited` when the daily quota would be exceeded |
| SearXNG | 1 req/s | Local |
| yt-dlp | 2 parallel downloads | |

## Circuit breaker

Per platform: 5 failures within 5 minutes → **open** for 10 minutes (tasks get `platform_unavailable` immediately) → **half-open**: one probe → close or re-open. State goes into `platform_health` in the Master's RunContext.

## Caching

`tool_cache` keyed by `(tool, normalized input)`. TTL: search/hashtag/sound = 6 h, creator = 24 h, trends = 12 h, video metadata = 6 h, `web_fetch` = 24 h. A cached hit is still recorded in `queries_done` for ownership.

## Normalization rules

- Canonical IDs: `tiktok:<video_id>`, `instagram:<shortcode>`, `youtube:<video_id>`.
- Hashtags lowercase without `#`; handles lowercase without `@`; sounds as `sound:<id>` / `audio:<id>`.
- Times in UTC ISO-8601.

## Optional later

- Apify actors as a paid fallback per platform (default **off**, explicit budget required).
