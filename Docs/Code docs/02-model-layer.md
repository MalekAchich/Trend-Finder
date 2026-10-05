# Code/02: Model Layer

**Status:** Draft v2. Q-01 closed: adapters follow D-29/D-30/D-31 and `claude-codex-auth-reference.md`.

## Goals

- One interface for both subscriptions (D-02, D-03).
- Use the strongest available models per role, discovered at runtime.
- Never crash a run because of usage limits; move work or pause and resume.

## Provider adapter interface

```python
class ProviderAdapter(Protocol):
    name: Literal["claude", "chatgpt"]

    async def list_models(self) -> list[ModelInfo]: ...
    async def complete(self, req: CompletionRequest) -> CompletionResponse: ...
    async def health(self) -> ProviderHealth: ...
```

- `CompletionRequest`: model, system, messages (text + images), tools (JSON Schema), tool_choice, max_output_tokens, reasoning effort (if supported), timeout.
- `CompletionResponse`: text parts, tool calls (normalized `{id, name, arguments}`), stop reason, usage (tokens if the provider returns them), raw response reference (for debugging), rate-limit info parsed from headers when present.
- **Internal message format is provider-neutral.** Each adapter translates it to its own wire format.
- `CompletionResponse.tool_calls` is filled natively (ChatGPT) or by **parsing the emulated step JSON** (Claude, D-30), so the agent loop never knows which provider it talks to.

## Adapters

| Adapter | Built on | Notes |
|---|---|---|
| `ClaudeCLIAdapter` | `claude -p --model <id> --system-prompt … --output-format json --permission-mode dontAsk --tools "" --no-session-persistence --json-schema <step schema>` | The Claude CLI owns the credentials (`claude auth login --claudeai`). Images are written to temp files and referenced as `@/path`. Tool calling is emulated (D-30): the step schema forces `{"type":"tool_calls",…}` or `{"type":"final",…}`. Models are discovered from `~/.claude/stats-cache.json`, with aliases `opus`/`sonnet`/`haiku` as fallback. |
| `ChatGPTOAuthAdapter` | OAuth PKCE (loopback `127.0.0.1:1455`) or device code → `POST chatgpt.com/backend-api/codex/responses` (SSE, `store:false`) | Our own token file `secrets/chatgpt-auth.json` (chmod 600), refreshed 90 s before expiry under a lock. Native function tools; images as `input_image` data URLs; structured output via `text.format` json_schema. Models from `GET /models?client_version=…` (several versions tried, largest list kept, cached to disk). |
| `FakeAdapter` | Scripted responses | Tests only (`09-testing.md`). |

Login endpoints (`Backend`): `GET /api/providers/{p}/status`, `POST /api/providers/chatgpt/login/start {device_code}`, `POST /api/providers/claude/login/start`, `POST /api/providers/claude/login/complete {code}`, `POST /api/providers/{p}/logout`. Pending logins live in the single backend process.

## Model registry

- On startup and every 30 min: `list_models()` per provider → upsert into `models` (provider, model_id, display name, last_seen).
- **Capabilities** (vision, tool calling, context window, reasoning effort) are merged from a local override file `config/model_capabilities.yaml`, because the model-list callback may not report them.
- A model missing from the latest list is marked unavailable; the router skips it.

## Role router

`config/roles.yaml`, an ordered preference list per role. The first available model with enough capacity wins:

```yaml
master:       [{provider: claude, model: best}, {provider: chatgpt, model: best}]
scout:        [{provider: chatgpt, model: best}, {provider: claude, model: best}]
deep_dive:    [{provider: claude, model: best}, {provider: chatgpt, model: best}]
radar:        [{provider: chatgpt, model: fast}, {provider: claude, model: fast}]
seed_study:   [{provider: claude, model: best}, {provider: chatgpt, model: best}]
analyst:      [{provider: claude, model: best}, {provider: chatgpt, model: best}]
cross_check:  {use: other_provider_than_primary_judge}
curator:      [{provider: claude, model: best}]
brief:        [{provider: claude, model: best}, {provider: chatgpt, model: best}]
learner:      [{provider: claude, model: best}, {provider: chatgpt, model: best}]
```

- `best` / `fast` are aliases resolved through `config/model_aliases.yaml` against the registry (e.g. `best → claude-opus-5-5`), so model upgrades are a one-line change.
- **Load balancing:** scouts and deep dives alternate providers by default, so both accounts are used in parallel.
- Every result records the provider and model used.

## Usage governor

- **Per-provider semaphore** (default 4 concurrent calls each; configurable).
- **Usage-limit detection:** provider-specific errors and rate-limit headers are normalized to `UsageLimited(reset_at | None)`.
  - `reset_at` known → provider `cooling_until = reset_at`.
  - Unknown → probe with one small call every 15 min.
- **Rerouting:** a role falls back to the next provider in its list. If none is available, the orchestrator moves the run to `paused_usage` and resumes automatically when a provider recovers.
- **Transient errors** (5xx, timeouts, network): exponential backoff with jitter, at most 3 attempts; then the call fails up to the agent loop.
- **Auth errors:** no retry; the provider is marked `auth_error` and the UI shows a re-login prompt.

## Call ledger

Every call is logged to `model_calls`: run, task, role, provider, model, input/output tokens (if returned), image count, latency, status, error class. No prompt text in the ledger (transcripts are stored separately and compressed). The Live Run screen shows calls and tokens per provider.

## Security

- Credentials stay where the owner's auth code keeps them (or in `secrets/`, git-ignored). They are never written to the DB, logs, transcripts or prompts.
- Adapters accept no URLs from model output; only the tool layer makes network calls on the agents' behalf.
