# Code/09: Testing Strategy

**Status:** Draft v1, awaiting review

Test what is expensive or invisible when it breaks: redundancy, lost work, usage handling, scoring math and scraper normalization. No tests for cosmetic UI details.

## Test doubles

| Double | Replaces | Use |
|---|---|---|
| `FakeAdapter` | Provider adapters | Scripted responses: tool calls, `submit_result`, invalid JSON, `UsageLimited`, timeouts |
| Fixture tools | Platform tools | Recorded, sanitized JSON responses per platform (`tests/fixtures/platforms/`) |
| Fixture media | Downloads | 5 short clips: single-person dance, two people, heavy cuts, moving camera, no audio |

## Unit tests

- **Scoring:** sub-score formulas, percentile normalization, overall weights, freshness decay, explore-ratio function, Thompson sampling with a fixed seed.
- **Normalization:** canonical IDs, hashtag/handle/sound normalization, URL → canonical ID per platform.
- **Plan validation:** overlapping scopes rejected per task, explore/exploit share enforced, caps enforced, near-duplicate direction keys merged.
- **Schemas:** every agent result schema accepts valid examples and rejects common malformed outputs.
- **Retention:** quota eviction order and protected media (👍, seeds, top cards).

## Integration tests (Postgres in Docker, FakeAdapter, fixture tools)

1. **Full run happy path:** plan → parallel workers → analysis → curation → trend cards; asserts no duplicate clusters and that every card has 4 sub-scores and justifications.
2. **Non-redundancy:** two workers find the same video → one finding plus added evidence; a repeated query returns `duplicate_of_task`.
3. **Crash recovery:** kill the worker pool mid-round → leases expire → tasks resume → the run completes with no duplicate findings.
4. **Usage limits:** provider A returns `UsageLimited` → work moves to B; both limited → `paused_usage` → auto-resume.
5. **Platform failure:** TikTok fixture errors → breaker opens → the run completes with the other platforms, and `platform_health` reaches the Master.
6. **Invalid agent output:** one repair attempt, then the task fails with partial results kept.
7. **Feedback loop:** submitting ratings updates direction alpha/beta, creates a taste profile version, and changes the next run's `explore_ratio`.
8. **Pipeline on fixture media:** feasibility metrics are within the expected ranges per clip; the hard filter rejects the two-person and heavy-cut clips.

## Live smoke tests (manual, opt-in, `-m live`)

- `tf doctor` passes.
- One tiny real call per provider (`list_models` + a 1-step completion).
- One real search per platform, 5 results, normalized correctly.
- One real end-to-end run with caps `rounds=1, tasks=3, target_findings=5`.

Live tests never run in the default test command.
