# Socials page: our own channels, their numbers, and what they teach the agents

> Owner direction, 2026-10-08: "we've created our instagram and tiktok channel and posted our first video. fully build the socials page now so that I connect the accounts and we add a data intel to the system".
> Approach chosen by the owner: the platforms' official APIs, with public numbers as the fallback ("whatever you think is strongest and best").

## What it is for

- The owner runs one posting channel per platform per character (now: Vice Nicolaiz on Instagram and TikTok; YouTube and X later).
- The Socials page shows how each channel and each post performs, like the platforms' own creator consoles, in one place.
- What works on our own channels becomes **intel for the agents**: the formats our audience rewards weigh in when runs judge new videos.

**Success:**
- The owner connects both channels in minutes.
- The page fills in on its own and stays current.
- Every post shows its numbers over time.
- The next run's judges know which of our own posts did best and why.

## Hard rules

- **Posting channels are never automated.**
  - No browser logins, no posting, liking, following or commenting.
  - We only read, through the official APIs or the public pages.
  - The scraping accounts stay separate (D-35, D-38).
- **Tokens and app secrets live only in `secrets/`** (0600), like the other credentials. They never go in the DB, logs, prompts or API responses.
- **The owner's posts are never judged** (same spirit as D-56). The numbers are facts, and the agents learn from them; nobody rates the owner's videos.

## Connecting a channel

Each channel has a state, from least to most connected:

1. **Public: a handle only.**
   - **Works:** immediately, with no setup.
   - **Reads:**
     - followers and post count;
     - per post: views (TikTok plays, Instagram plays where the page shows them), likes and comments.
   - **How:** read-only, through the existing scraping sessions' creator feeds (`instagram_creator`, `tiktok_creator`, D-52). If a session isn't connected, it falls back to yt-dlp per post.
2. **Official API.**
   - **Instagram** (Instagram API with Instagram login; the account must be Professional: Creator or Business):
     - **App setup:** the owner creates a free Meta developer app with the Instagram product. They add their account as an Instagram tester and accept the invite in Instagram.
     - **Connecting:** the owner generates an access token in the app dashboard and pastes it into the app. The app checks it, saves it, and refreshes it before it expires (60 days; `refresh_access_token`).
     - **Why a pasted token:** it avoids an OAuth redirect, which Meta may refuse to send to a local `http` address.
     - **Fallback:** if the dashboard's token button turns out to be gone, implementation adds the OAuth flow with an `https` callback.
     - **Account data:** followers, media count.
     - **Per post (insights):** views, reach, likes, comments, shares, saved, total interactions, average watch time and total watch time (reels).
   - **TikTok** (Login Kit for Desktop + Display API; scopes `user.info.basic`, `user.info.profile`, `user.info.stats`, `video.list`):
     - **App setup:** the owner creates a free TikTok developer app of the Desktop type, in sandbox mode with their own account as a target user, and pastes its client key and secret into the app.
     - **Connecting:** "Connect" opens TikTok's consent page. The redirect comes back to `http://127.0.0.1:8000/api/socials/tiktok/callback`; desktop apps may use loopback addresses, with PKCE. Tokens refresh automatically.
     - **Account data:** followers, likes, video count.
     - **Per video:** views, likes, comments, shares. TikTok's Display API has no watch time.
- **Partial grants:** the owner may refuse a scope. The page then says what's missing and falls back to public numbers for it.
- **Guides:** each "Connect" panel walks through the steps with direct links, written as short numbered instructions in the app.

## Sync

- **Schedule:** every 3 hours while the app runs, plus a "Sync now" button. The first sync runs right after connecting.
- **What a sync does:** reads the account and every post from the last 90 days, and stores one snapshot per post per sync. Snapshots stop for posts older than 90 days; their history stays.
- **Failures:**
  - A failure (expired token, revoked access, rate limit) is shown on the channel card with a clear fix, such as "reconnect".
  - Other channels keep syncing.
  - API limits are respected; at this account size that's a few calls per sync.

## Data (migration 0007)

- `social_channels`: platform, handle, character_id, mode (public / api), connected_at, last_synced_at, last_error, granted scopes. The tokens themselves stay in `secrets/`.
- `social_posts`: channel_id, platform post id, canonical_id (links to `videos` when known), url, caption, posted_at, thumbnail path, duration_s, and `inspired_by` (a found or manually chosen video's canonical_id, set by the owner, optional).
- `social_snapshots`: post_id, taken_at, views, reach, likes, comments, shares, saves, avg_watch_s, total_watch_s.
- `social_channel_snapshots`: channel_id, taken_at, followers, total likes, post count.

## The page

- **One section per character** (now one), with a card per channel: avatar, handle, mode badge (Public / Official API), followers with change over 7 days, last sync, and Connect / Sync now / Disconnect.
- **Headline numbers** for the chosen window (7 days / 30 days / all):
  - views, followers gained and engagement rate, from (likes + comments + shares + saves) / views;
  - average watch time and watch-through (avg watch / length) where Instagram gives it.
- **Views over time:** one line per post, starting from when it was posted, so the owner sees which post is still climbing.
- **Posts:** cards with the thumbnail, hover playback (the existing previews), the numbers, the change since the previous day and the age.
  - Each card has an optional **"Recreated from…"** picker listing the found and manually chosen videos, plus Video / Sound downloads.
  - Clicking a card opens its numbers over time.
- **Empty and error states:** a channel with no posts yet, a public-only channel ("connect the official API for reach, saves and watch time"), and a sync error.

## Intel for the agents

- **What's computed:** after each sync, a short per-character **channel report**, with no model call. It covers:
  - the channels' size and growth;
  - each recent post's numbers, ranked by views per hour and engagement;
  - what each post is: its format, trend type and tags, from a one-time study of the post. This is the same study the references get, never a verdict.
  - the "recreated from" link, when the owner set it.
- **Where it goes:**
  - into every judge's brief (analyst, second opinion, look check), as: "Your own channels: … Best: the gas-station dance, 12.4k views in 2 days, 9% engagement, 61% watch-through (recreated from @ai_aniketzade's reel)…";
  - into the learner's taste profile, as evidence alongside the owner's thumbs.
- **What the judges are told:** formats that did well on our own channels are strong signals; formats that did poorly are weak signals, never vetoes, because one post is a small sample.
- **On the page:** a "What the agents learned from your channels" box, like the Characters page's taste box.

## API

- `GET /api/socials`: characters → channels with their latest numbers.
- `POST /api/socials/channels` `{platform, handle, character}`: add a public channel.
- `DELETE /api/socials/channels/{id}`: disconnect; the token is deleted and the history kept.
- `POST /api/socials/channels/{id}/instagram-token` `{token}`: check and save.
- `PUT /api/socials/tiktok-app` `{client_key, client_secret}`; `GET /api/socials/tiktok/connect?channel=` (redirects to TikTok); `GET /api/socials/tiktok/callback`.
- `POST /api/socials/channels/{id}/sync`.
- `GET /api/socials/channels/{id}/posts?window=`: posts with their latest numbers and deltas.
- `GET /api/socials/posts/{id}/history`: snapshots.
- `PATCH /api/socials/posts/{id}` `{inspired_by}`.
- `GET /api/socials/report?character=`: the channel report text.

## Testing

- **Parsers:** made-up API answers for the Instagram Graph and TikTok Display responses, plus public-page captures, in `tests/tools/samples.py` (visible, explained).
- **Token handling:** refresh before expiry, a revoked token marks the channel, and secrets never appear in API output.
- **Sync on the test DB:** snapshots, deltas and the 90-day window.
- **Report text and brief injection.**
- **API:** endpoints, including the TikTok PKCE round-trip against a fake TikTok.
- **Frontend:** vitest for the number formatting and deltas; a screenshot check of the page.
- **Live:** once the owner connects (Public first, then each official API), a real sync, checked against the numbers the apps show.

## Not in this step

- YouTube and X channels: the same shape, added when those channels exist.
- Posting or scheduling posts.
- Instagram follower demographics and TikTok Business API audience data.
