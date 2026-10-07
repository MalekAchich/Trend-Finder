# Plan 7: Agent context, accounts & keys, X, premium platform buttons: Implementation Plan

> **For agentic workers:** executed inline with superpowers:executing-plans (TDD per task). The owner gave the direction in chat (2026-10-07). No agent test runs until the owner provides the keys and says go; single tiny provider calls that check a protocol are fine.

**Goal:** Before the first real test runs:
- each agent keeps its own model and its own reasoning for its whole task;
- the search tools can use a YouTube Data API key, TikTok Creative Center, and logged-in scraping accounts for TikTok, Instagram and X;
- X is a fourth platform;
- Settings has an "Accounts & keys" section for easy rotation;
- the Home platform buttons use each platform's logo and colours.

**Owner decisions (2026-10-07):**
- **TikTok:** TikTok Creative Center (public, official trend data) plus a throwaway scraping account. The Research API is ruled out: it's academic-only, the regions exclude Tunisia, and commercial use is excluded.
- **Context fixes now,** before the first tests.
- **Home:** only the platform buttons change (brand colour and logo).
- **Socials and monetization:** monetization rules are written down for the future Socials page. The Socials page itself isn't built.

## Global Constraints
- Credentials live only under `secrets/` (files mode 0600). They're never stored in the DB, written to logs or put in prompts. The API never returns a secret; only a masked hint (the last 4 characters) and timestamps.
- Logins happen in a real browser window the owner controls. Passwords are never stored, only the session (Playwright storage state plus a cookies.txt for yt-dlp).
- Credentials are read at use time, so a rotation takes effect without a restart.
- A refused answer is still never handed to another provider (D-46).
- Higgsfield stays manual. Only the Trend Finder DB is touched.

## Review Focus
1. An agent's provider never changes mid-conversation. At a usage limit, the task restarts fresh on the other provider with a consolidated brief, never with the other model's transcript.
2. ChatGPT reasoning items go back on the next step only when they came from ChatGPT. A Claude session is resumed only by the same agent, and its file is deleted when the agent ends.
3. No secret value appears in any API response, log line, event or exception message.
4. X URLs work everywhere a platform URL is accepted: canonical id, targets, embed, filters.
5. The YouTube quota tracker refuses calls that would exceed the daily quota. An invalid key gives a clear "key rejected" message.

---

### Task 1: Agent context: pinning, clean restart, reasoning kept
- **Pinning (`loop/agent.py`):** after step 1, every step uses `only=<step-1 provider>`.
- **Usage limit mid-task:** if the pinned provider hits its limit (`AllProvidersUnavailable` with `only`), the loop restarts once on any other available provider with a fresh conversation:
  - the original task;
  - a "Progress so far" digest: tools called with their arguments, a short result for each, and the agent's notes;
  - an emitted `provider.switched` event, reason "restarted fresh".
  - The remaining step budget carries over.
- **ChatGPT:**
  - The request sends `include: ["reasoning.encrypted_content"]`.
  - Reasoning output items (with encrypted content) are returned in `CompletionResponse.provider_state`, stored on the assistant `Message` as `provider_state["chatgpt"]`, and sent back before that turn's function calls (without `id`).
  - Ignored for other providers.
- **Claude:**
  - `CompletionRequest.conversation` (an agent-loop id) maps to one CLI session per conversation: the first call uses `--session-id`, later calls use `--resume` and send only the messages added since the last call.
  - If the history changed (compaction), it starts a new session with the full flattened prompt.
  - `ModelClient.end_conversation(id)` deletes the session file.
  - At startup, stale session files in the runtime cwd's project folder (> 24 h) are swept.
  - Session persistence is on only for conversations.
- **Scoring:** the analyst provider is pinned per run (the first available in the role order). At its limit, the pin moves once for the rest of the run, with an event. `analyst_model` is already recorded per score.
- **Tests:**
  - pin after step 1;
  - restart brief contents, and no foreign transcript;
  - ChatGPT payload carries reasoning items only for chatgpt turns;
  - SSE parse of reasoning items;
  - Claude argv for first vs resumed calls, the delta prompt, and the reset on divergence;
  - session cleanup;
  - analyst pin and its single move.

### Task 2: Credentials store + browser sessions
- **`tf_agent/credentials.py`:** `Credentials(secrets_dir)`.
  - Keys: `youtube_api_key`.
  - Sessions: `tiktok`, `instagram`, `x` in `secrets/sessions/<platform>/state.json` plus `cookies.txt`.
  - Operations: `get/set/delete`, `describe()` (masked), `has_session`, `cookies_file(platform)`.
  - Atomic writes, mode 0600.
- **`tf_agent/tools/browser.py`:** Python Playwright (dependency + Chromium).
  - `connect(platform)` opens a headed browser on the login page and waits (up to 10 min) for the platform's session cookie (`sessionid` for TikTok and Instagram, `auth_token` for X). It then saves the storage state and writes a Netscape cookies.txt.
  - `capture_json(platform, url, url_pattern, scrolls)` runs headless with the saved state and collects matching JSON responses.
  - A circuit-breaker per platform marks "session expired" on a login redirect.
- **Tests:**
  - masking, file modes, rotation;
  - cookies.txt format;
  - capture with a fake page;
  - nothing secret in `describe()`.

### Task 3: Search tools
- **YouTube Data API (`tools/youtube_api.py`):**
  - `search.list` (type=video, videoDuration=short, order=viewCount|date, publishedAfter from freshness, q, maxResults ≤ 50) then `videos.list` (statistics, contentDetails, snippet), keeping ≤ 180 s.
  - Quota tracker: 10,000 units a day (Pacific day), persisted in `settings.youtube_quota`.
  - `shorts_search` uses it when a key is set, otherwise the existing route.
- **TikTok:**
  - `tiktok_trends(kind=hashtags|songs|creators|videos, country, period)` from Creative Center pages through `capture_json` (no login).
  - `tiktok_search` uses the logged-in session (search page JSON) when connected, otherwise `site:`.
  - yt-dlp gets the TikTok cookies.
- **Instagram:** session cookies feed yt-dlp (metrics + analysis). `instagram_search` also captures keyword/hashtag reels JSON when connected.
- **X:**
  - `x_search(query, recent)` uses the logged-in search (`filter:native_video`, top) through `capture_json`, otherwise `site:x.com`.
  - Metadata comes through yt-dlp with X cookies.
- **Tests:** fixture JSON for each parser; key-less and session-less fallbacks; quota refusal.

### Task 4: X platform plumbing
- **Canonical ids and URLs:** `x:<status_id>` from `x.com` / `twitter.com` / `mobile.twitter.com` `/<user>/status/<id>`. The canonical URL is `https://x.com/i/status/<id>`.
- **Platform lists:** `Platform` literals, PlatformRegistry config, `PLATFORM_TOOLS["x"]`, runs API platforms (default stays tiktok/youtube/instagram, X opt-in), and CLI search.
- **Prompts:** planner and worker prompts know X.
- **Frontend:** types, labels, the embed (`platform.twitter.com/embed/Tweet.html?id=`), and URL validation.
- **Tests:** normalize table, API accepts X, embed URL.

### Task 5: Settings "Accounts & keys" + premium platform buttons
- **API:**
  - `GET /api/settings/accounts`: per item: id, label, kind (key/session), set, hint, updated_at, status.
  - `PUT /api/settings/accounts/youtube_api_key {value}`.
  - `DELETE /api/settings/accounts/{id}`.
  - `POST /api/settings/accounts/{platform}/connect`: starts the browser window in the background; the status is polled.
- **UI:** one card per item: masked hint, Replace / Remove for the key, Connect / Reconnect / Disconnect for sessions, and a short "what it unlocks" line.
- **Home buttons:** TikTok, Instagram, YouTube and X with their real logo marks. Selected: the brand colour (Instagram's gradient). Unselected: muted, with the logo in brand colour on hover.
- **Tests:**
  - accounts API with a fake store: masking, 422s, no secret in any response;
  - vitest for the platform list;
  - smoke checks the four buttons and the accounts section.

### Task 6: Monetization rules, docs, sweep, review
- `Docs/Research/monetization-rules.md`: per platform requirements, AI-content rules, regional availability, and the legitimate routes. Sources and dates included.
- Decisions D-48 onwards, docs 03 tools and 07 API, README.
- Ruff on the changed files and all tests, then a fresh reviewer and a fix pass.
