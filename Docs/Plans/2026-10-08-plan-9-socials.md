# Plan 9: Socials page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (native, inline; the owner said "build dude"). Steps use checkbox (`- [ ]`) syntax. TDD per task: failing test → minimal code → green → commit.

**Goal:** the owner connects their own Instagram and TikTok channels. The Socials page shows each channel's and each post's numbers over time, and a per-character channel report becomes intel for every judge and the learner.

**Architecture:**
- **`tf_agent/socials/`:**
  - readers per source: public pages, the Instagram Graph API and the TikTok Display API, all giving one `ChannelRead` shape;
  - a token store under `secrets/socials/`;
  - a `SocialSync` that writes snapshots to four new tables;
  - a `channel_report()` text builder.
- **The backend:** a `socials` API router and a background sync loop (every 3 h) started in the app lifespan.
- **The frontend:** replaces the Socials placeholder page.

**Tech Stack:**
- Python 3.12, SQLAlchemy async + Alembic, httpx, FastAPI.
- Existing pieces: Playwright `BrowserSessions` (public reads), React 19 + TanStack Query + Tailwind 4, and an inline SVG chart (no new chart dependency).

**Spec:** `Docs/Specs/2026-10-08-socials-page.md`

## Global Constraints
- **Posting channels are never automated:** no browser logins on them, and no posting, liking, following or commenting. Only official APIs or public pages are read.
- **Tokens and app secrets live only in `secrets/`** (files 0600, folders 0700). They never go in the DB, logs, prompts or API responses; the API returns at most a masked hint.
- **The owner's posts are never judged:** numbers are facts. Studies of own posts describe them (format, tags), with no verdict or score.
- **Sync:** every 3 hours, plus "Sync now". Posts from the last 90 days get snapshots; older posts keep their history.
- **Instagram:** Instagram API with Instagram login (graph.instagram.com), pasted long-lived token, auto-refresh before expiry (60 days, `refresh_access_token`).
- **TikTok:** Login Kit for Desktop + PKCE, redirect `http://127.0.0.1:8000/api/socials/tiktok/callback`, scopes `user.info.basic,user.info.profile,user.info.stats,video.list`.
- **Test data:** made up and visible in `Agent/tests/tools/samples.py` (no copied real data, no magic ids).
- **Only the Trend Finder DB** (`trendfinder` / `trendfinder_test`).

## Review Focus
1. **A token expires or is revoked between syncs:** the channel shows "reconnect", other channels keep syncing, and public numbers still fill in. Pinned in Task 4.
2. **The same post appears from two sources** (public + API) or across syncs: one post row, one snapshot per sync, never duplicates. Pinned in Task 4.
3. **A channel with zero posts, or a post with missing metrics** (Instagram hides plays on public pages, TikTok has no watch time): the page and report show "–", not 0 or a crash. Pinned in Tasks 4 and 8.
4. **A TikTok callback with a wrong or replayed `state`, or a refused scope:** rejected with a clear message, and partial scopes are recorded and shown. Pinned in Task 6.
5. **Secrets leaking into API output or logs** (token, client secret): never. The API test greps every response. Pinned in Task 6.

---

### Task 1: Tables (migration 0007 + models)

**Files:**
- Create `Database/migrations/versions/0007_socials.py` and `Database/tf_db/models/socials.py`.
- Modify `Database/tf_db/models/__init__.py`.
- Test: `Database/tests/test_socials_models.py`.

**Produces:**
- `SocialChannel(id, character_id→characters CASCADE, platform str16, handle str64, mode str8 'public'|'api', connected_at, last_synced_at, last_error text, scopes JSONB list, created_at)`, unique `(platform, handle)`.
- `SocialPost(id, channel_id→CASCADE, platform_post_id str64, canonical_id str64 null, url text, caption text, posted_at, thumbnail_url text, duration_s float, inspired_by str64 null, study JSONB null, created_at)`, unique `(channel_id, platform_post_id)`.
- `SocialSnapshot(id, post_id→CASCADE, taken_at, views, reach, likes, comments, shares, saves, avg_watch_s float, total_watch_s float)`, index `(post_id, taken_at)`.
- `SocialChannelSnapshot(id, channel_id→CASCADE, taken_at, followers, total_likes, posts)`.

**Steps:**
- [ ] Test: insert a channel, post and snapshots on the test DB; the unique constraints hold; deleting the channel cascades.
- [ ] Write the migration and models (UUID7 ids like the others).
- [ ] Green, then commit.

### Task 2: Token store

**Files:**
- Modify `Agent/tf_agent/credentials.py`: add a `socials` section.
- Test: `Agent/tests/test_credentials.py`.

**Produces:**
- `Credentials.social_token(channel_id) -> dict | None`.
- `save_social_token(channel_id, data: dict)` (`access_token`, `refresh_token?`, `expires_at`, `scopes`).
- `delete_social_token(channel_id)`.
- `tiktok_app() -> dict | None` and `save_tiktok_app(client_key, client_secret)`.
- `describe_socials() -> list[dict]`: masked hints only.

**Files on disk:** `secrets/socials/<channel_id>.json` and `secrets/socials/tiktok_app.json`, all 0600 inside a 0700 folder.

**Steps:**
- [ ] Test the permissions, the round trip, delete, and that describe never contains the secret.
- [ ] Implement, go green, commit.

### Task 3: Readers (one shape from three sources)

**Files:**
- Create `Agent/tf_agent/socials/__init__.py`, `types.py`, `instagram_api.py`, `tiktok_api.py` and `public.py`.
- Modify `Agent/tf_agent/tools/session_search.py`: profile stats parsers.
- Modify `Agent/tests/tools/samples.py`.
- Test: `Agent/tests/socials/test_readers.py`.

**Produces:**
- `types.py`:
  - `PostRead(platform_post_id, url, caption, posted_at, thumbnail_url, duration_s, views, reach, likes, comments, shares, saves, avg_watch_s, total_watch_s)`, all metric fields `int|float|None`;
  - `ChannelRead(handle, followers, total_likes, posts_count, posts: list[PostRead], scopes: list[str])`;
  - `ReadError(code: Literal['expired','revoked','rate_limited','unavailable','not_professional'], message)`.
- `InstagramApi(client: httpx.AsyncClient, base="https://graph.instagram.com")`:
  - `me(token)`;
  - `read(token, since: datetime) -> ChannelRead`: `/me` fields `username,followers_count,media_count`; `/me/media` fields `id,permalink,caption,timestamp,media_type,media_product_type,thumbnail_url,like_count,comments_count`, paged until older than `since`;
  - per post `/{id}/insights?metric=views,reach,likes,comments,shares,saved,total_interactions`, plus for reels `ig_reels_avg_watch_time,ig_reels_video_view_total_time`, in ms → s;
  - a metric the API rejects is retried without it (Meta rotates metrics);
  - `refresh(token) -> dict` (`/refresh_access_token?grant_type=ig_refresh_token`).
- `TikTokApi(client, base="https://open.tiktokapis.com")`:
  - `authorize_url(client_key, redirect, state, challenge) -> str`;
  - `exchange(app, code, verifier, redirect) -> dict`;
  - `refresh(app, refresh_token) -> dict`;
  - `read(token, since) -> ChannelRead`: `/v2/user/info/?fields=open_id,username,display_name,avatar_url,follower_count,likes_count,video_count`; `POST /v2/video/list/?fields=id,share_url,video_description,create_time,cover_image_url,duration,view_count,like_count,comment_count,share_count`, cursor-paged;
  - TikTok `error.code` → `ReadError`.
- `public.py`: `PublicReader(browser: BrowserSessions)` with `read(platform, handle) -> ChannelRead`. It uses `capture_json` on the creator URLs, `session_search.instagram_items` / `tiktok_items`, and new parsers `instagram_profile_stats(captured, handle)` / `tiktok_profile_stats(captured, handle) -> dict` (followers, total likes, posts).

**Steps:**
- [ ] Test, with made-up JSON in samples (`ig_me`, `ig_media_page`, `ig_insights`, `tt_user_info`, `tt_video_list`, `ig_profile_capture`, `tt_profile_capture`) and an `httpx.MockTransport`:
  - parsing, paging and stopping at `since`;
  - ms → s conversion;
  - a rejected metric retried;
  - TikTok error mapping;
  - missing metrics are `None`, not 0.
- [ ] Implement, go green, commit.

### Task 4: Sync

**Files:**
- Create `Agent/tf_agent/socials/sync.py`.
- Test: `Agent/tests/socials/test_sync.py`.

**Consumes:** Tasks 1–3.

**Produces:** `SocialSync(sm, creds, instagram: InstagramApi, tiktok: TikTokApi, public: PublicReader, clock)`:
- `sync_channel(channel_id) -> SyncResult(posts, new_posts, error)`:
  - **API mode:** refreshes a token within 7 days of expiry. On a `ReadError(expired|revoked)` it sets `last_error="reconnect: …"`, keeps the mode and falls back to the public reader for that sync.
  - **Public mode:** reads public pages only.
  - **Writes:** upserts posts on `(channel_id, platform_post_id)` and sets `canonical_id` via `normalize.canonical_id(url)`. Adds one `SocialSnapshot` per post, skipping a post that already has one within 30 minutes (Review Focus 2), and only for posts from the last 90 days. Adds one `SocialChannelSnapshot`. Sets `last_synced_at` and clears `last_error` on success.
- `sync_all()`: every channel, one failure never stops the others.

**Steps:**
- [ ] Test with fake readers:
  - snapshots and the window;
  - dedupe (the same post twice, two syncs within 30 minutes);
  - an expired token marks "reconnect" and the public numbers are still saved (Review Focus 1);
  - refresh happens near expiry;
  - a zero-post channel works (Review Focus 3);
  - one channel failing doesn't stop the next.
- [ ] Implement, go green, commit.

### Task 5: Channel report + intel for the agents

**Files:**
- Create `Agent/tf_agent/socials/report.py`.
- Modify `Agent/tf_agent/orchestrator/run.py` (`with_references` → also the report).
- Modify `Agent/tf_agent/learning/learner.py` (the taste refresh reads the report).
- Modify the prompts `analyst.md` and `cross_check.md` (one paragraph).
- Test: `Agent/tests/socials/test_report.py`, `Agent/tests/orchestrator/test_lookalike_run.py`.

**Produces:**
- `async channel_report(sm, character_id, now) -> str | None`:
  - the channels' size and 7-day growth;
  - the last 30 days' posts ranked by views per hour, then engagement ((likes+comments+shares+saves)/views);
  - each post's numbers, the age, the format and tags from `study` when present, and "recreated from <inspired_by>" when set;
  - `None` with no channels; "–" for unknown numbers.
- `with_references(ch, run)` appends `report` when present: "Your own channels (facts, not verdicts): …".
- **Prompt line:** "Formats that did well on the owner's own channels are strong signals; poor ones are weak signals, never vetoes (one post is a small sample)."

**Steps:**
- [ ] Test:
  - the report ranking and text, missing metrics shown as "–";
  - a lookalike run's analyst brief contains the report when a channel exists.
- [ ] Implement, go green, commit.

### Task 6: API

**Files:**
- Create `Backend/tf_backend/api/socials.py`.
- Modify `Backend/tf_backend/main.py`, `app_context.py` and `context_builder.py` (the `socials` service + a 3-hour loop in the lifespan).
- Test: `Backend/tests/test_socials_api.py`.

**Produces the endpoints** listed in the spec, under `/api/socials`:
- `GET ""` → `{characters:[{slug,name,channels:[ChannelOut]}], tiktok_app:{set,hint}}`.
- `ChannelOut`: `id, platform, handle, mode, connected_at, last_synced_at, last_error, scopes, followers, followers_7d, views_7d, posts`.
- `POST /channels` (409 when duplicate).
- `DELETE /channels/{id}`: deletes the token, keeps the history.
- `POST /channels/{id}/instagram-token`: verifies with `me()`, exchanges a short-lived token if given, saves, sets mode api, syncs.
- `PUT /tiktok-app`.
- `GET /tiktok/connect?channel=`: 302 to TikTok; `state` and the PKCE verifier are kept in memory for 10 minutes, single-use.
- `GET /tiktok/callback`: checks `state`, exchanges, saves the token and granted scopes, syncs, then 302 to `/socials?connected=tiktok`.
- `POST /channels/{id}/sync`.
- `GET /channels/{id}/posts?window=7d|30d|all`: latest numbers, the change in 24 hours, and the engagement rate.
- `GET /posts/{id}/history`.
- `PATCH /posts/{id}` `{inspired_by}`.
- `GET /report?character=`.

All writes need the client header.

**Steps:**
- [ ] Test with fake readers and a fake TikTok token endpoint:
  - every endpoint;
  - wrong, replayed and expired `state`, and a refused scope recorded (Review Focus 4);
  - the secret-leak grep over every response body (Review Focus 5).
- [ ] Implement, go green, commit.

### Task 7: Studies of own posts

**Files:**
- Modify `Agent/tf_agent/socials/sync.py`: after a sync, posts with no `study` are studied once (a background task), using `roles.study_seed` on a contact sheet from `ManualVideos`' check path, or `get_video` + `Analyzer` frames.
- Test: `Agent/tests/socials/test_sync.py`.

**Steps:**
- [ ] Test: a new post gets one study (a fake role), never twice, and a failure leaves `study=None` without breaking the sync.
- [ ] Implement, go green, commit.

### Task 8: Socials page

**Files:**
- Modify `Frontend/src/pages/Socials.tsx`, `src/api/hooks.ts` and `src/api/types.ts`.
- Create `src/components/socials/ChannelCard.tsx`, `ConnectPanel.tsx`, `ViewsChart.tsx` (inline SVG lines, one per post, x = hours since posting), `PostCard.tsx` and `src/lib/socials.ts` (formatting, deltas, engagement).
- Test: `src/lib/socials.test.ts`.

**The page:**
- one section per character, with channel cards and a "+ Add channel" (platform + handle);
- connect panels:
  - **Instagram:** numbered steps with links (Meta developer app → Instagram product → add your account as tester → accept in Instagram → generate a token) and a token field;
  - **TikTok:** steps (developer app, Desktop type, Login Kit + Display API, sandbox target user, redirect URI shown with a copy button), client key/secret fields and a Connect button;
- a window toggle (7 days / 30 days / All) and headline numbers;
- views over time;
- post cards: hover preview, numbers with 24-hour change, a "Recreated from…" select over found and manual videos, downloads;
- a "What the agents learned from your channels" box (the report);
- empty and error states.
- Design follows the app's dark top / paper bottom pattern.

**Steps:**
- [ ] vitest for `formatDelta`, `engagementRate` (unknowns → "–") and `watchThrough`.
- [ ] Build the page; `tsc`, vitest and build green.
- [ ] Screenshot check with Playwright, then commit.

### Task 9: Docs, live check

- [ ] Update `Docs/02-decisions.md` (D-58: Socials, official APIs + public fallback, the report as intel), the README nav line, the `07-api-and-frontend.md` endpoints and the plan index.
- [ ] Run the full suites, restart the server, add the owner's channels in public mode, sync live and compare with the apps.
- [ ] Commit, push.
