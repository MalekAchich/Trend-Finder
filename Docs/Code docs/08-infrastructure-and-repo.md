# Code/08: Infrastructure & Repository

**Status:** Draft v1, awaiting review. Library versions are pinned during implementation.

## Dev machine (checked 2026-10-05)

| Resource | Value | Consequence |
|---|---|---|
| CPU | 16 threads, x86_64 | Fine for parallel agents and 2 pipeline jobs |
| RAM | 15 GB (~6 GB free while working) | Max 2 Playwright contexts, 2 pipeline jobs |
| GPU | GTX 1650 4 GB, **driver not loaded** | Pipeline runs on CPU (Q-12) |
| Disk | **~19 GB free** on `/home` | Media quota 5 GB (D-26, Q-14) |
| Installed | Python 3.12.3, Node 22, Docker 29 + Compose v5, ffmpeg/ffprobe, yt-dlp, git | Need to install: `uv`, Playwright Chromium, Chromaprint (`fpcalc`) |

## Repository layout (D-32: owner's folders)

```
Trend Finder App/
├── pyproject.toml                # uv workspace root: members Database, Agent, Backend
├── .env.example · .gitignore
├── config/                       # roles.yaml, model_aliases.yaml, model_capabilities.yaml, searxng/settings.yml
├── Docs/                         # this documentation ("Code docs/" = technical docs)
├── Database/                     # package tf_db
│   ├── docker-compose.yml        # postgres (pgvector) + searxng
│   ├── pyproject.toml
│   ├── alembic.ini · migrations/
│   └── tf_db/                    # SQLAlchemy models, session, queue SQL, repositories
├── Agent/                        # package tf_agent: the multi-agent engine
│   ├── pyproject.toml
│   └── tf_agent/
│       ├── models/               # provider adapters (claude_cli, chatgpt_oauth, fake), registry, router, governor, ledger
│       ├── loop/                 # run_agent(): step protocol (native + emulated tool calls), compaction, submit_result
│       ├── roles/                # master, radar, seed_study, scout, deep_dive, analyst, curator, brief, learner
│       ├── prompts/              # <role>.md (versioned)
│       ├── tools/                # web, tiktok, instagram, shorts, video; limiter, breaker, cache, blackboard filter
│       ├── pipeline/             # probe, scenes, frames, sheet, whisper, pose, motion, fingerprint, retention
│       ├── scoring/              # sub-scores, overall, clustering, bandit, explore ratio
│       ├── learning/             # learner glue, weight suggestion
│       ├── characters/           # folder sync, profile parser
│       └── orchestrator/         # run state machine, rounds, queue workers, caps, plan validation, events
│   └── tests/
├── Backend/                      # package tf_backend
│   ├── pyproject.toml
│   └── tf_backend/
│       ├── main.py               # FastAPI app; lifespan starts the orchestrator
│       ├── api/                  # characters, runs, trends, feedback, briefs, providers, platforms, settings, media, events (SSE)
│       └── cli.py                # `tf` command (Typer)
│   └── tests/
├── Frontend/                     # Vite + React + TS + Tailwind + shadcn/ui
├── media/                        # git-ignored: videos/, frames/, sheets/
└── secrets/                      # git-ignored: chatgpt-auth.json, browser-profiles/
```

Dependency direction: `Backend → Agent → Database`. `Database` imports nothing from the others.
Characters stay outside: `CHARACTERS_DIR="../AI Influencers Characters"`.

## Docker Compose services

| Service | Image | Port | Notes |
|---|---|---|---|
| `db` | `pgvector/pgvector:pg17` | 5432 | Named volume; healthcheck |
| `searxng` | `searxng/searxng` | 8888 | JSON format enabled in `config/searxng/settings.yml`; local only |

The backend and frontend run natively (D-25).

## Environment (`.env`)

```
DATABASE_URL=postgresql+asyncpg://tf:tf@localhost:5433/trendfinder
SEARXNG_URL=http://localhost:8888
CHARACTERS_DIR=../AI Influencers Characters
MEDIA_DIR=./media
MEDIA_QUOTA_GB=5
YOUTUBE_API_KEY=
CLAUDE_BIN=claude
CHATGPT_AUTH_FILE=./secrets/chatgpt-auth.json
MAX_AGENT_WORKERS=8
PER_PROVIDER_CONCURRENCY=4
BROWSER_CONTEXTS=2
PIPELINE_WORKERS=2
```

## Key dependencies

- **Backend:** fastapi, uvicorn, pydantic v2, sqlalchemy[asyncio], asyncpg, alembic, httpx, sse-starlette, typer, playwright, yt-dlp, trafilatura, faster-whisper, mediapipe, opencv-python-headless, pillow, imagehash, pyacoustid (Chromaprint), scikit-learn (weight suggestion), pytest, pytest-asyncio.
- **Frontend:** vite, react, typescript, tailwindcss, shadcn/ui, @tanstack/react-query.

## CLI (`tf`)

| Command | Purpose |
|---|---|
| `tf doctor` | Check DB, SearXNG, ffmpeg, fpcalc, Playwright, provider auth, platform logins, disk quota |
| `tf login tiktok\|instagram` | One-time visible-browser login for the throwaway profile |
| `tf sync-characters` | Import/refresh characters from the folder |
| `tf run <slug> [--platforms …]` | Start a run headless (same engine as the UI) |
| `tf migrate` | Alembic upgrade |
| `tf media gc` | Force retention cleanup |

## Dev workflow

```
docker compose -f Database/docker-compose.yml up -d     # Postgres (pgvector) on 127.0.0.1:5433; SearXNG arrives in Plan 2
uv sync
cp .env.example .env           # once
uv run tf migrate
uv run tf login chatgpt        # once; `--device` if port 1455 is busy
uv run tf doctor
uv run uvicorn tf_backend.main:app --reload
uv run pytest                  # unit + DB tests; live tests: uv run pytest -m live Agent/tests/live
```
