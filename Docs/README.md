# Trend Finder: Documentation

**Status:** Spec stage, draft v1 (2026-10-05). No code yet; awaiting review.

Trend Finder is a local, personal, multi-agent app that finds viral trends and specific target videos (TikTok, Instagram Reels, YouTube Shorts) matching a given AI influencer character. It ranks them with explainable scores, learns from per-finding feedback, and turns approved findings into production briefs for manual Higgsfield / Kling 3.0 Motion Control production.

## Reading order

| # | File | What it covers |
|---|---|---|
| 1 | [01-product-brief.md](01-product-brief.md) | Purpose, inputs/outputs, run loop, success criteria, scope |
| 2 | [02-decisions.md](02-decisions.md) | Decision log: what was decided and why |
| 3 | [03-open-questions.md](03-open-questions.md) | Unresolved items, blockers first |
| 4 | [04-character-profile-format.md](04-character-profile-format.md) | How a character folder and `profile.md` are structured |
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
- Character data lives outside the app in `../AI Influencers Characters/<Name>/` (see `04-character-profile-format.md`).
