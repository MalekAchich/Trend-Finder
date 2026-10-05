# 03: Open Questions

**Status:** Live list. When answered, record the answer as a decision in `02-decisions.md` and move the question to Closed.

## Open

| ID | Question | Notes |
|---|---|---|
| Q-03 | Throwaway TikTok / Instagram accounts for logged-in scraping | **Deferred.** Anonymous mode first (D-34). Plug in later with `tf login tiktok\|instagram`. |
| Q-04 | YouTube Data API key | **Deferred to launch** (owner: "launch factor"). Shorts uses yt-dlp `ytsearch` until then (D-34). |
| Q-10 | Target audience region and language for trends | Default: language EN, no region filter. |
| Q-11 | Initial seed videos for Nicolaiz | Owner supplies 3–10 URLs when ready; runs work without seeds. |
| Q-12 | Fix the NVIDIA driver (GTX 1650)? | Optional; the pipeline runs on CPU. |
| Q-14 | Only ~19 GB of free disk | The media quota protects it; freeing space is advised. |

## Defaults adopted (change in Settings at any time)

| ID | Item | Value |
|---|---|---|
| Q-05 | Target trend cards per run | 20 |
| Q-06 | Max concurrent agent workers | 8 total / 4 per provider |
| Q-07 | Max rounds per run / tasks per round | 3 / 12 |
| Q-08 | Run wall-clock limit | 60 minutes |
| Q-09 | Media quota | 5 GB |

## Closed

| ID | Answer |
|---|---|
| Q-01 | Auth reference supplied → D-29, D-30, D-31 |
| Q-02 | The niche is discovered by the agents → D-33 |
| Q-13 | Git: the app root becomes a git repository at the start of implementation |
