# Code/06: Data Model (PostgreSQL 17 + pgvector)

**Status:** Draft v1, awaiting review. ORM: SQLAlchemy 2 (async) + asyncpg; migrations: Alembic. IDs are UUIDv7 unless noted. All timestamps are `timestamptz` (UTC).

## Characters & learning

| Table | Key columns |
|---|---|
| `characters` | id, slug (unique), name, folder_path, status, created_at |
| `character_versions` | id, character_id, version, profile_md, front_matter (jsonb), canonical_image_path, content_hash, created_at |
| `seeds` | id, character_id, url, canonical_id, source (file/url), analysis_id, study (jsonb, from seed_study), created_at |
| `taste_profiles` | id, character_id, version, body_md, author (`learner`/`owner`), source_run_id, created_at |
| `directions` | id, character_id, key (unique per character), label, hypothesis, niche, alpha, beta, runs_count, created_at, last_used_at |

## Runs & orchestration

| Table | Key columns |
|---|---|
| `runs` | id, character_id, character_version_id, state, settings (jsonb: platforms, caps, explore_ratio), explore_ratio_used, current_round, stop_reason, started_at, finished_at |
| `rounds` | id, run_id, number, plan (jsonb WorkPlan), plan_rejections (jsonb), summary (jsonb RoundSummary), master_call_id, started_at, finished_at |
| `tasks` | id, run_id, round_id, task_type, direction_id, platform, scope (jsonb), goal, budget (jsonb), state (`queued/running/succeeded/failed/cancelled`), lease_until, attempts, provider, model, result (jsonb), error (jsonb), transcript_path, prompt_version, created_at, started_at, finished_at |
| `scope_claims` | run_id, platform, kind (`query/hashtag/sound/creator/seed/radar/lead`), value, task_id — **unique (run_id, platform, kind, value)** |
| `leads` | id, run_id, source_task_id, platform, type, value, why, status (`new/assigned/done/dropped`), assigned_task_id |
| `queries_done` | run_id, tool, normalized_input_hash, task_id, cache_key, created_at |
| `events` | id (bigserial), run_id, type, payload (jsonb), created_at (SSE replay source) |

## Videos, findings, trends

| Table | Key columns |
|---|---|
| `videos` | canonical_id (PK), platform, url, creator_handle, caption, hashtags (text[]), sound_id, posted_at, duration_s, metrics (jsonb), metrics_at, first_seen_at |
| `video_analyses` | id, canonical_id, pipeline_version, probe, cuts, transcript, pose, camera_motion, best_clean_segment, feasibility, fingerprint (all jsonb/numeric), contact_sheet_path, media_path, created_at |
| `findings` | id, run_id, task_id, canonical_id — **unique (run_id, canonical_id)** — direction_id, why, evidence (jsonb), preliminary_fit, status (`pending_analysis/filtered_feasibility/analyzed/failed`) |
| `finding_scores` | finding_id, fit, fit_breakdown (jsonb), fit_justification, adaptation_idea, feasibility, momentum, freshness, overall, analyst_provider, analyst_model, cross_fit, cross_provider, disagreement (bool) |
| `trend_clusters` | id, run_id, label, best_finding_id, member_count, sound_id, overall, rank |
| `trend_members` | cluster_id, finding_id |
| `seen_items` | character_id, canonical_id, first_run_id, rated (bool) — blackboard for cross-run "already seen" |

## Feedback, briefs, ledger

| Table | Key columns |
|---|---|
| `card_feedback` | id, cluster_id, rating (`up/down/skip`), note, created_at |
| `run_feedback` | run_id (PK), satisfaction (1–10), note, created_at |
| `briefs` | id, cluster_id, character_version_id, body (jsonb + md), provider, model, created_at |
| `models` | provider, model_id (PK pair), display_name, capabilities (jsonb), available, last_seen_at |
| `provider_state` | provider (PK), status (`ok/cooling/auth_error`), cooling_until, last_error, updated_at |
| `model_calls` | id, run_id, task_id, role, provider, model, input_tokens, output_tokens, images, latency_ms, status, error_class, created_at |
| `platform_state` | platform (PK), health (`ok/degraded/unavailable/needs_login`), breaker_state, opened_at, quota_used_today |
| `tool_cache` | key (PK), tool, response (jsonb), expires_at |
| `settings` | key (PK), value (jsonb): weights, thresholds, caps, roles override |

## Designed-for (empty in v1)

| Table | Purpose |
|---|---|
| `publications` | cluster_id/brief_id → platform post URL, posted_at |
| `post_metrics` | publication_id, observed_at, views, likes, shares, watch-time fields |
| `embeddings` | (object_type, object_id) → `vector` (pgvector), for later retrieval |

## Key indexes

`tasks (state, lease_until)` for queue claims · `findings (run_id, status)` · `videos (sound_id)` · `seen_items (character_id, canonical_id)` unique · `events (run_id, id)`.

## Queue claim (D-23)

```sql
UPDATE tasks SET state = 'running', lease_until = now() + interval '10 minutes', attempts = attempts + 1
WHERE id = (
  SELECT id FROM tasks
  WHERE run_id = $1 AND (state = 'queued' OR (state = 'running' AND lease_until < now()))
  ORDER BY created_at
  FOR UPDATE SKIP LOCKED
  LIMIT 1)
RETURNING *;
```

Workers renew the lease every 2 minutes while running.
