# 01: Product Brief

**Status:** Draft v1, awaiting review

## Purpose

Find the trends and specific videos an AI influencer character should recreate next, and explain why. The character is the anchor: a finding is only good if it fits the character's persona, energy and charisma, and can actually be produced with Kling 3.0 Motion Control.

First character: **Nicolaiz**, a deadpan 1970s gentleman doing comedy (Jean-Phil–like energy, different niche/context).

## Where it sits in the bigger pipeline

```
Trend Finder ──► Production brief ──► Manual Higgsfield / Kling production ──► Edit ──► Post ──► Views
   (this app)       (this app)            (human, outside the app)            (later: metrics feed back)
```

## User

One person (the owner), running locally on their own machine. No multi-user support, no hosting.

## Inputs

| Input | Source | Required |
|---|---|---|
| Character pack: canonical image, reference sheets, `profile.md` | `AI Influencers Characters/<Name>/` | Yes |
| Seed videos: viral examples that show the intended direction | URLs or files added per character or per run | Recommended |
| Character socials and published videos | Links in `profile.md` | Optional (none exist yet for Nicolaiz) |
| Run settings: platforms, target number of findings, budget caps, explore override | New Run form | Defaults provided |
| Feedback from previous runs: per-finding ratings, notes, overall satisfaction | Stored in DB | Automatic |

## Outputs

1. **Trend cards**, ranked. One card per trend cluster (the same dance/format across reposts), each with:
   - best source URL (cleanest version for motion reference) plus alternates
   - platform, creator, metrics (views, engagement, age, sound, hashtags)
   - **overall match score** and **sub-scores** (character fit, Kling feasibility, momentum, freshness)
   - justification per sub-score, and an "how Nicolaiz would do it" adaptation idea
   - source status label (owned / stock / unknown); informational, the user decides
   - contact sheet (frame grid) and embedded video
2. **Production briefs**, only for 👍 findings: shot plan, Kling settings, prompt, framing, what to record yourself.
3. **Learning artifacts**: updated taste profile and direction stats after each feedback session.

## The run loop

```
New run
 → Master agent ingests inputs, thinks, divides the work, spawns N parallel workers
 → Workers search/scrape via tools, follow leads, return candidates + leads
 → Every candidate is analyzed (deterministic pipeline, then an AI judge)
 → Master reviews the round: spawn another wave, or stop
 → Curator clusters, cross-checks the top results with the other provider, ranks
 → User reviews: 👍/👎 per finding + optional note + overall satisfaction (1–10)
 → Learner updates the taste profile, direction stats, scoring suggestions
 → Next run: satisfaction sets explore vs. exploit; the taste profile steers the Master
```

## Success criteria (v1 acceptance)

1. A default run on Nicolaiz produces **≥ 20 trend cards** across TikTok, Instagram and Shorts, each with sub-scores and justifications.
2. **≥ 50% of the top 10** cards pass the deterministic Kling-feasibility check (one person, body visible, limited occlusion/camera motion).
3. **No duplicates**: no two cards in a run share a trend cluster; videos already rated in earlier runs never resurface as new.
4. **Feedback changes behavior**: after rating a run, the next run's explore ratio, direction choices and taste profile change in a way that can be inspected in the UI.
5. **Parallel work without redundancy**: workers in a run never own overlapping scopes (same platform + query/hashtag/sound/creator).
6. **Resilience**: a backend restart mid-run resumes it; one platform scraper failing does not fail the run; hitting one provider's usage limit moves work to the other provider or pauses and resumes.
7. A 👍 finding produces a production brief on demand.

Goal (not an acceptance test): overall satisfaction trends upward across the first 5 runs.

## Scope

**v1:** everything above, for one or more characters, three platforms, two model providers.

**Designed for, built later:** import of published-video metrics (found → produced → posted → views), trained ranking classifier (≥ 200 ratings), pgvector retrieval (≥ 500 ratings), paid scraping fallback (Apify), scheduled runs.

**Non-goals:** auto-posting, Higgsfield automation (its unlimited plan forbids scripting), multi-user, cloud hosting, n8n.
