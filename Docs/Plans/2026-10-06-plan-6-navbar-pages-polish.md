# Plan 6: Navbar, pages, settings and polish: Implementation Plan

> **For agentic workers:** executed inline with superpowers:executing-plans (TDD per task). Task-spec format. The owner gave the design in chat (2026-10-06) and answered the open questions; no live agent runs until the owner asks (paused by the owner).

**Goal:** Turn the single page into a small app with a top navbar and the owner's logo:
- **Home:** the current page.
- **Characters:** images, what the agents learned, and found videos filtered by found date.
- **Runs:** history; opening a run replays its stream and shows its videos.
- **Settings:** live usage of both subscriptions, average tokens per run, model choice per provider (main + fast + effort).
- **Socials:** empty for now.

Also polish the composer, the character cards and the seam (smooth gradient, grey-ish lower zone, a yellow pulse when a video is saved).

**Owner decisions (2026-10-06):**
- Character cards show the whole image, in its own shape and uncropped.
- Models are chosen per provider: a main model, a fast model and an effort.
- Found videos stay on Home and also appear on Characters for the selected character, with a found-date filter.
- Socials stays empty.
- The logo is `Frontend/src/Logo/trend-finder-animated.svg`. Its animation plays once on first render and loops while an agent run is active.

## Global Constraints
- Everything from Plan 5 still holds (images-only characters, URL-only videos, hybrid theme, no generator traces).
- The navbar stays small (52 px).
- Model choices persist in `settings` (key `models`) and apply to new model calls immediately. They're never hard-coded in the UI.
- Usage numbers come from what the providers actually report:
  - **ChatGPT:** `x-codex-*` headers give % used, the window and the reset time.
  - **Claude:** the CLI reports no percentage. Show its status (ready / at its limit until …) and the token counts from our own call ledger.

## Review Focus
1. A model the owner picked disappears from the provider's list → calls fall back (registry behaviour); Settings shows the choice as unavailable.
2. An effort level the model doesn't support → rejected with a clear 422.
3. Opening an old run in Runs → the stream replays from the start, and the agents end as done or failed, never stuck on "working".
4. The found-date filter on Characters → only videos found in the window, newest first.
5. Reduced motion → no logo loop, no seam pulse.

---

### Task 1: Backend: model settings, usage, runs list, character detail and videos
- **Model choices:**
  - `GET /api/settings/models`: per provider: models (id, name, effort levels), current `main` / `fast` / `effort`, and the allowed efforts.
  - `PUT /api/settings/models`: body `{provider, main, fast, effort|null}`. The models are validated against the provider's list, and the effort against the levels allowed for the chosen models (for Claude: low…max).
  - Choices are stored in `settings.models` and applied to `registry.aliases[provider]` (`best` = main, `fast` = fast) and to `ModelClient.effort_override[provider]`.
  - The choices are loaded at startup.
- **Claude adapter:** passes `--effort` when the request carries an effort.
- **Client:** effort override, applied as `override or role effort`.
- **`GET /api/usage`:** per provider: status, used % / window / reset time when known, cooling until, last error; plus tokens and calls today, and the average total tokens per run over the last 20 runs, from `model_calls`.
- **`GET /api/runs?character=&limit=`:** id, character card, state, stop reason, started, finished, videos, satisfaction, tokens.
- **`GET /api/characters/{slug}`:** name, images, the latest read (from the newest run that has one), the latest taste profile body (owner notes included), runs, videos found.
- **`GET /api/characters/{slug}/videos?days=`:** found videos across runs, newest found first; curated cards when the run is curated, otherwise the live set. `FoundVideo` gains `found_at`.
- **Tests:** each route (happy path + errors), alias/effort application, Claude `--effort` argv, average tokens per run, days filter.

### Task 2: Frontend: router, navbar with animated logo, pages
- **Routing and navbar:** `react-router-dom`. The navbar (52 px) holds the logo (inline SVG component), then Home, Characters, Runs, Socials, Settings, with the run status and provider dots on the right.
- **Logo animation:**
  - The intro plays once on first mount.
  - While a run is active, the logo remounts every cycle so the animation loops.
  - When idle, the infinite idle loops in the SVG are disabled.
- **Characters page:** a character switcher with whole-image cards, an image gallery, "What the agents see" (the latest read), "What they learned" (the taste profile), and found videos with a filter (Today, 7 days, 30 days, All).
- **Runs page:** a table of runs filterable by character. Opening one (`/runs/:id`) shows the Engine replay plus that run's videos and score.
- **Settings page:**
  - usage cards per provider (% bar for ChatGPT, status for Claude, tokens today, average tokens per run);
  - model pickers per provider (main, fast, effort) with a Save button.
- **Socials page:** empty-state page.
- **Tests (vitest):** the logo loop controller, the stream reducer marking working agents done when the run ends, and the days filter helper.

### Task 3: Polish: composer, cards, seam
- **Character cards:** keep the image's aspect ratio, uncropped (`object-contain` inside a frame shaped like the image).
- **Optional inputs:** both sections can be open at once, with an animated height (grid-rows 0fr → 1fr) and roomier inner padding.
- **Seam:**
  - Oklch-interpolated gradient with many stops plus a fine noise overlay, so there's no banding.
  - The lower zone becomes a light grey-ish tone with a faint lime/yellow glow.
- **Library background:** the same grey-ish tone, with white cards.
- **Video saved:** a yellow-ish pulse sweeps the seam from top to bottom (it replaces the orb), then the card enters.
- **Tests:** Playwright smoke at 1440 / 1280 / 390: no horizontal scroll, navbar routes, both inputs open together, no console errors, screenshots reviewed.

### Task 4: Docs, sweep, final review
- Update 07, the README and decisions (D-44…).
- Ruff on changed files; search for the generator's name.
- Fresh reviewer (after the Claude window resets), then a fix pass.
