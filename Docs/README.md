# Trend Finder: Documentation

**Status:** Built (Plans 1–6 done, 2026-10-06). The docs describe the shipped system; each doc's Status line says how closely.

Trend Finder is a local, personal, multi-agent app that finds viral trends and specific target videos (TikTok, Instagram Reels, YouTube Shorts) matching a given AI influencer character. Characters are just images; the agents read them, discover the niche, and rank videos with explainable scores, learning from per-video 👍/👎 and notes. The owner then recreates the picks manually with Higgsfield / Kling 3.0 Motion Control.

## Quick start

Needs Docker, [uv](https://docs.astral.sh/uv/), Node 22, ffmpeg, the Claude CLI signed in, and a ChatGPT subscription.

```bash
cd "Trend Finder App"
docker compose -f Database/docker-compose.yml up -d   # Postgres on 127.0.0.1:5433 + SearXNG on :8888
uv sync && cp .env.example .env                       # once
uv run tf migrate
uv run tf login chatgpt                               # once (Claude uses the CLI's own login)
uv run tf doctor                                      # everything green?
(cd Frontend && npm install && npm run build)          # once, and after frontend changes
uv run tf serve                                       # opens http://127.0.0.1:8000
```

In the app (one page):

1. Add a character: a folder of images in `AI Influencers Characters/<Name>/`. That's all a character is.
2. Pick the character. Optionally paste trending AI-influencer videos and target videos you found (each tagged with a character).
3. **Start run** and watch the agents live: the character read, plans, every thought, tool call and rejection.
4. Saved videos fly into **Found videos** below (character → run → videos). Hover a card to preview it.
5. Rate videos 👍/👎 with an optional note, and score the run 1–10. The next run starts by learning from that.

The navbar also has **Characters** (what the agents see and learned, found videos by date), **Runs** (history and replays), **Settings** (live usage, model choice per subscription) and **Socials** (coming later).

Everything also works from the terminal: `tf run nicolaiz --trend-url <url> --target <url>[=character]`, `tf runs`, `tf trends <run>`, `tf resume <run>`.

## Reading order

| # | File | What it covers |
|---|---|---|
| 1 | [01-product-brief.md](01-product-brief.md) | Purpose, inputs/outputs, run loop, success criteria, scope |
| 2 | [02-decisions.md](02-decisions.md) | Decision log: what was decided and why |
| 3 | [03-open-questions.md](03-open-questions.md) | Unresolved items, blockers first |
| 4 | [04-character-folder.md](04-character-folder.md) | A character = a folder of images; what the Reader derives from them |
| 5 | [Code docs/00-architecture-overview.md](Code%20docs/00-architecture-overview.md) | System diagram, components, run lifecycle |
| 6 | [Code docs/01-agents.md](Code%20docs/01-agents.md) | Master/worker multi-agent design, task types, non-redundancy |
| 7 | [Code docs/02-model-layer.md](Code%20docs/02-model-layer.md) | Provider adapters, model registry, router, usage governor |
| 8 | [Code docs/03-tools.md](Code%20docs/03-tools.md) | Search/scraping tool contracts per platform |
| 9 | [Code docs/04-video-pipeline.md](Code%20docs/04-video-pipeline.md) | Deterministic video analysis (frames, transcript, pose) |
| 10 | [Code docs/05-scoring-and-learning.md](Code%20docs/05-scoring-and-learning.md) | Sub-scores, feedback, taste profile, explore/exploit |
| 11 | [Code docs/06-data-model.md](Code%20docs/06-data-model.md) | PostgreSQL tables |
| 12 | [Code docs/07-api-and-frontend.md](Code%20docs/07-api-and-frontend.md) | REST/SSE API and UI pages |
| 13 | [Code docs/08-infrastructure-and-repo.md](Code%20docs/08-infrastructure-and-repo.md) | Repo layout, Docker, env, machine constraints |
| 14 | [Code docs/09-testing.md](Code%20docs/09-testing.md) | Test strategy |
| 15 | [Code docs/claude-codex-auth-reference.md](Code%20docs/claude-codex-auth-reference.md) | Owner-supplied reference: Claude CLI + ChatGPT OAuth subscription auth |

## Conventions

- Decisions go in `02-decisions.md` with an ID (`D-xx`). Docs reference IDs instead of re-arguing.
- Open items go in `03-open-questions.md` (`Q-xx`). Close them by moving the answer into a decision.
- Docs are living: update them during development when reality differs from the plan. Each doc has a **Status** line.
- Character data lives outside the app in `../AI Influencers Characters/<Name>/` (see `04-character-folder.md`).
