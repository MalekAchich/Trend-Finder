# Code/05: Scoring & Learning

**Status:** Draft v1, awaiting review

## Sub-scores (all 0–100) (D-17)

| Sub-score | Source | Definition |
|---|---|---|
| **Character fit** | Analyst (AI vision), averaged with the cross-check for the top K | Rubric 0–10 each: persona match, deadpan/contrast potential, energy compatibility, niche/context fit, adaptability to the character's look → mean × 10 |
| **Kling feasibility** | Pipeline (`04-video-pipeline.md`) | Formula from pose, cuts, motion; the Analyst can add notes but doesn't change the number |
| **Momentum** | Tool metadata | `0.7 × pct(views_per_hour) + 0.3 × pct(engagement_rate)`, percentiles computed per platform over all cached items from the last 30 days |
| **Freshness** | Tool metadata + clusters | `age_decay × (1 − 0.5 × saturation_pct)`; age_decay = 1.0 for ≤ 3 days, falling linearly to 0.2 at 30 days; saturation = cluster size / sound uses percentile |

- `views_per_hour = views / max(hours_since_post, 1)`
- `engagement_rate = (likes + comments + shares + saves) / views`, ignoring null fields

**Overall score** (initial weights, editable in Settings):

```
overall = 0.40·fit + 0.30·feasibility + 0.20·momentum + 0.10·freshness
```

**Source status** (owned / stock / unknown) is a label only (D-27).

Each trend card shows one-line justifications per sub-score: the Analyst writes the fit line, and templates generate the others from the numbers (e.g. "2.1M views in 3 days: top 4% momentum on TikTok").

## Cross-provider check (D-20)

- Applies to the top `K = 15` clusters by overall score.
- The other provider's model scores character fit **blind** (it doesn't see the first score).
- `|fit_a − fit_b| ≥ 30` → flag "models disagree" on the card, with both justifications.
- Final fit = mean of the two.

## Clustering (D-19)

Two findings join the same cluster if any of these hold:
1. same canonical ID (cross-posted duplicate), or
2. same sound/audio ID **and** perceptual frame-hash distance below threshold, or
3. audio fingerprint match **and** a Curator AI confirmation ("same trend/format?") for borderline cases.

Best source per cluster = the highest `feasibility`, ties broken by resolution, then momentum.

## Feedback model (owner choice: C)

- Per trend card: **👍 / 👎 / skip** plus an optional note.
- Per run: **overall satisfaction 1–10** plus an optional note.
- Ratings attach to the trend card and propagate to its member findings and to their **origin directions** and tasks.

## Learner (after feedback)

Inputs: the run's cards with ratings and notes, sub-scores, directions, the previous taste profile, overall satisfaction.

Outputs:
1. **New taste profile version** (markdown, ≤ 500 words, sections: *Loves*, *Avoid*, *Open hypotheses*, *Owner notes* (verbatim, newest first)). The owner can edit it in the UI; edits create a new version.
2. **Direction stats update:** for each direction, `alpha += 👍 count`, `beta += 👎 count`.
3. **Weight suggestion** (shown, never auto-applied): after ≥ 30 rated cards, a logistic regression of 👍/👎 on the four sub-scores → suggested normalized weights next to the current ones, with an "Apply" button.
4. **Run retrospective** (3–5 bullets) shown on the run page and given to the next Master call.

## Explore vs. exploit (D-18)

```
explore_ratio = clamp(0.85 − 0.075 × (satisfaction − 1), 0.15, 0.85)
  satisfaction 1 → 0.85    5 → 0.55    10 → 0.175
first run, or fewer than 3 directions with ratings → explore_ratio = 1.0
```

- The orchestrator draws a Thompson sample `θ ~ Beta(1 + alpha, 1 + beta)` per existing direction and passes the ranked list to the Master.
- The Master fills `(1 − explore_ratio)` of the tasks from the top-ranked directions (exploit) and the rest with new directions (explore).
- The owner can override `explore_ratio` in New Run.

## In-context examples (instead of RAG, D-09)

Every Master and Analyst call for a character includes the **10 most-liked and 10 most-disliked cards** (title, sub-scores, owner note), chosen from the most recent 5 runs first.

## Later (designed for, not built)

| Feature | Trigger |
|---|---|
| Trained ranking classifier on sub-scores + metadata features | ≥ 200 ratings |
| pgvector retrieval of similar past findings | ≥ 500 ratings or cross-character learning |
| Real performance signal: found → produced → posted → views, used as a stronger label than 👍 | Once videos are published; `publications` + `metrics` tables are already in the schema |
