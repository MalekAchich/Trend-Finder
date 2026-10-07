# Implementation Plans

| Plan | Scope | Status |
|---|---|---|
| [Plan 1](2026-10-05-plan-1-foundation-and-model-layer.md) | Workspace, DB foundation, both subscriptions behind one adapter interface, router/governor, agent loop, provider API, `tf` CLI | Done, reviewed |
| [Plan 2](2026-10-05-plan-2-tools-and-video-pipeline.md) | Tool layer (anonymous mode, D-34) + video pipeline | Done, reviewed |
| [Plan 3](2026-10-06-plan-3-orchestrator-roles-scoring.md) | Orchestrator, Master/worker roles, scoring, clustering | Done, reviewed |
| [Plan 4](2026-10-06-plan-4-feedback-api-frontend.md) | Feedback & learning, briefs, run API + SSE, React web app, `tf serve` | Done, reviewed (UI and briefs replaced by Plan 5) |
| [Plan 5](2026-10-06-plan-5-single-page-redesign.md) | Single page, images-only characters, live agent stream, URL-only found videos ([spec](../Specs/2026-10-06-single-page-redesign.md)) | Done |
| [Plan 6](2026-10-06-plan-6-navbar-pages-polish.md) | Navbar + logo, Characters / Runs / Settings / Socials pages, model choice, usage, polish | Done |
| [Plan 7](2026-10-07-plan-7-context-accounts-x.md) | One model per agent conversation, reasoning kept; Accounts & keys (YouTube API, TikTok/Instagram/X accounts); X platform; brand platform buttons; monetization rules | In review |

Deferred by the owner: throwaway TikTok/Instagram logins (`tf login tiktok|instagram`), the YouTube Data API key, and TikTok Creative Center (needs Playwright).
