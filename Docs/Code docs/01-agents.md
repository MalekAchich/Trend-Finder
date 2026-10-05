# Code/01: Agents (Master / Worker)

**Status:** Draft v2. Loop decided: D-30/D-31. Niche discovery: D-33.

## Principles

1. **The Master thinks and divides; workers execute; the orchestrator enforces.** (D-04, D-05)
2. **Every worker owns a disjoint scope.** Redundancy is rejected by code, not discouraged by prompt. (D-07)
3. **Workers never spawn workers.** They return leads; the Master decides. (D-06)
4. **Every agent ends by calling `submit_result`** with schema-validated JSON. Free text is never parsed.
5. **Caps live outside the models.** Steps, rounds, workers, time and usage are all enforced by the orchestrator. (D-22)

## Agent roster

| Agent | When | Model tier | Inputs | Output |
|---|---|---|---|---|
| **Master** | Start of run, then each round boundary | Strongest | `RunContext` (+ `RoundSummary` after round 1) | `WorkPlan` |
| **Worker: radar** | Usually round 1 | Strong/fast | Platform + region | Trending sounds, hashtags, formats on that platform |
| **Worker: seed_study** | Round 1, one per seed | Strong + vision | One seed's pipeline output | Patterns: format, hook, why it works, search angles |
| **Worker: scout** | Any round | Strong | Direction × platform scope | Candidates + leads |
| **Worker: deep_dive** | Round 2+ | Strong | One lead (sound / hashtag / creator / format) | Candidates + leads |
| **Analyst** | Automatically, per candidate that passes the feasibility filter | Strong + vision | Contact sheet, transcript, metadata, pose stats, character brief + canonical image | Character-fit score, adaptation idea, feasibility notes, justification |
| **Curator** | End of run | Strongest (+ other provider for cross-check) | All analyzed findings | Clusters, best source per cluster, final ranking |
| **Brief Writer** | On 👍 | Strongest + vision | Trend card + character pack | Production brief |
| **Learner** | After feedback | Strongest | Run findings + ratings + notes, previous taste profile | New taste profile version, stat updates, weight suggestions |

Role-to-model mapping is configured in the role router (`02-model-layer.md`), not hardcoded.

## RunContext (input to the Master)

```json
{
  "run_id": "…",
  "character_brief": "…compact profile, ~600 tokens…",
  "canonical_image": "<image>",
  "seeds": [{"seed_id": "…", "url": "…", "summary": "…from seed_study, if already analyzed…"}],
  "taste_profile": "…current version, markdown…",
  "examples": {"liked": ["…top 10 with notes…"], "disliked": ["…top 10 with notes…"]},
  "directions": [{"direction_id": "…", "key": "…", "label": "…", "alpha": 6, "beta": 2, "runs": 3}],
  "explore_ratio": 0.45,
  "platforms": ["tiktok", "instagram", "youtube_shorts"],
  "platform_health": {"tiktok": "ok", "instagram": "degraded", "youtube_shorts": "ok"},
  "caps": {"max_tasks_this_round": 12, "rounds_left": 3, "target_findings": 20},
  "provider_capacity": {"claude": "ok", "chatgpt": "cooling until 14:20"}
}
```

## WorkPlan (output of the Master)

```json
{
  "reasoning_summary": "Short public rationale shown in the UI.",
  "directions": [
    {"direction_id": null, "key": "deadpan-mundane-duty", "label": "Mundane tasks done with ceremony",
     "hypothesis": "Contrast between gravity and trivial actions suits his engine.", "niche": "deadpan professional", "mode": "explore"},
    {"direction_id": "d_12", "mode": "exploit"}
  ],
  "tasks": [
    {"task_type": "scout", "direction_key": "deadpan-mundane-duty", "platform": "tiktok",
     "scope": {"queries": ["serious man dance trend", "deadpan dance"], "hashtags": ["#deadpan"], "sounds": [], "creators": []},
     "goal": "Find single-person, full-body deadpan dance trends from the last 14 days.",
     "max_candidates": 8}
  ],
  "stop": false,
  "stop_reason": null
}
```

Rules the Master is told, and the orchestrator checks:
- The share of tasks in `explore` directions ≈ `explore_ratio` (±1 task).
- Exploit directions are chosen with Thompson-sampled direction stats (the orchestrator pre-computes the sample and passes the ranked list).
- `direction_id: null` means a new direction. The orchestrator assigns IDs and fuzzy-matches `key` against existing directions to stop near-duplicates (the Master gets the existing list and must reuse IDs when it means the same thing).

## Task types

| Type | Scope key (ownership) | Typical tools | Typical budget |
|---|---|---|---|
| `radar` | `(platform, "radar", region)` | `tiktok_trends`, `shorts_search`, `web_search` | 15 steps |
| `seed_study` | `(seed_id)` | `get_video`, pipeline output, `web_search` | 10 steps |
| `scout` | `(platform, each normalized query / hashtag / sound / creator)` | platform search tools, `get_video`, `web_search`, `web_fetch` | 30 steps, ≤ 8 candidates |
| `deep_dive` | `(platform, lead type, lead value)` | platform tools for sound/hashtag/creator pages | 20 steps, ≤ 8 candidates |
| `analyze` | `(canonical_video_id)` | none (pipeline output given) | 1–2 calls |
| `cross_check` | `(trend_cluster_id)` | none | 1 call |
| `brief` | `(trend_card_id)` | none | 1–2 calls |
| `learn` | `(run_id)` | none | 1–2 calls |

## Non-redundancy (enforced by the orchestrator)

1. **Scope ownership.** Every scope element is normalized (lowercase, strip `#`/`@`, canonical sound ID, canonical creator handle) and registered as `(run_id, platform, kind, value)`. A plan containing a scope element already owned in this run is **rejected per task**; the Master gets the rejection list in the next call. Cross-run reuse is allowed only for exploit directions.
2. **Blackboard (shared, in Postgres).**
   - `seen_items`: canonical video IDs already found in this run or rated in any earlier run. Search tools **filter these out** and report `"hidden_already_seen": n`, so workers don't spend steps on them.
   - `queries_done`: every executed search (platform + normalized query). Running it again returns the cached result with `"duplicate_of_task": …`.
   - `leads`: leads submitted by workers, with status `new | assigned | done | dropped`. Only the Master assigns them.
3. **Candidate claim.** Submitting a candidate inserts its canonical ID with a unique constraint. The second submitter gets `already_submitted_by: <task>` and adds evidence instead of a duplicate.
4. **Cluster-level dedupe** happens in the Curator (D-19), catching reposts with different IDs.

## Worker loop

```
messages = [system(role prompt), user(task + compact character brief + canonical image if needed)]
for step in 1..max_steps:
    response = model.complete(messages, tools=role_tools + [submit_result], output=…)
    if response.calls submit_result → validate against schema
        ok → persist, done
        invalid → append validation error, allow 1 repair, else fail with partial
    execute tool calls (concurrently when independent) → append results (truncated to a token budget)
    emit progress event (step, tool, short summary) → SSE
at max_steps → force a final "submit now" turn
```

- Tool results are **summarized/truncated** before going back into the context (each tool defines a compact form).
- Each worker's transcript is stored (compressed) for debugging and shown in the Live Run drill-down.

## Worker result schema (`scout` / `deep_dive`)

```json
{
  "candidates": [
    {"url": "…", "platform": "tiktok", "canonical_id": "tiktok:7412…",
     "why": "Single-person deadpan dance, full body, static camera, 2.1M views in 3 days.",
     "evidence": {"views": 2100000, "posted_at": "…", "sound": "…", "hashtags": ["…"]},
     "preliminary_fit": 8}
  ],
  "leads": [{"type": "sound", "platform": "tiktok", "value": "sound:7290…", "why": "Same sound used by 4 deadpan hits"}],
  "notes": "Instagram results were mostly reposts of TikTok.",
  "coverage": {"queries_run": 6, "items_seen": 140, "hidden_already_seen": 22}
}
```

## Prompts

- Stored as files: `backend/app/agents/prompts/<role>.md`, with a version header. The prompt version is saved with every result.
- Each role prompt includes: the role's purpose, its scope boundary ("only your scope; return leads, don't chase them"), the character brief, scoring rubrics where relevant, and the `submit_result` schema.
- No prompt may contain secrets, tokens or file system paths outside `media/`.

## Agent-loop implementation (D-30, D-31)

- Our own loop over the provider adapters' normalized `complete()`; schemas are Pydantic models.
- **ChatGPT:** native Responses function tools; `submit_result` is a normal tool.
- **Claude:** emulated tools (D-36). Every step is one isolated `claude -p` call whose `--json-schema` forces
  `{"thought"?: str, "calls": [{"name": …, "arguments": {…}}]}`. `submit_result` is a normal tool for both providers.
  The transcript (compacted) is re-sent each step, since calls are stateless.
- The single entry point for the rest of the system:
  `run_agent(role, task, tools, result_schema, budget) -> AgentResult`

## Niche discovery (D-33)

When the character's niche is `OPEN`, the Master must spread explore directions across **at least 3 distinct niche hypotheses** per run and tag each direction with `niche`. Direction stats roll up by niche in the UI, so the winning niche becomes visible. The Learner proposes a primary niche once one niche holds ≥ 60% of 👍 across ≥ 2 runs.
