# Plan 8: Manually chosen videos

> Executed inline (TDD per task). Owner direction, 2026-10-07:
> - Videos the owner finds are kept in their own "Manually chosen videos" section, shown as cards. Each one can be intel for the agents (a reference), a target to recreate, or both.
> - Every video study also extracts niche, tags, trend type and audio use.
> - "Optional inputs" is renamed.

## Design
- **The list is permanent.**
  - Table `manual_videos` holds: url, canonical id, platform, `is_reference`, `target_character_id`, status (checking / ready / problem), problem text, thumbnail path, added at.
  - Adding the same video again only updates its roles.
- **Each link is checked as soon as it's added,** in the background: yt-dlp metadata (with the connected accounts' cookies) plus the platform's thumbnail, saved as ≤ 40 KB at 360 px. The card then shows the thumbnail, creator, views and length, or a clear problem such as private or login needed.
- **Reference.**
  - Every run studies the references it hasn't studied for that character yet, 2 at a time.
  - Studies are kept in `reference_studies` (manual video × character), so each video is studied once per character.
  - Every run's planner gets all studies for its character.
- **Target.**
  - Setting a target character creates a pending `targets` row, as before.
  - That character's next run analyses and scores it.
  - The card shows waiting, or the score.
- **Richer study (seed study and analyst):** niche, 3–10 topic tags of the model's own, trend type, audio use. The analyst's go into `finding_scores` (`tags`, `trend_type`, `audio_use`).
- **Run inputs:** `trend_urls` / `targets` on a run (API or CLI) are saved into `manual_videos` first. The run then uses the list.
- **Limits:** up to 100 links per add.
- **UI:**
  - The composer's section becomes "Your reference videos" ("not required, but the agents study these first"). Adding saves right away.
  - The Home library and the Characters page get two tabs: **Found videos** and **Manually chosen**.
  - Manually chosen cards show a hover preview, role badges, the status, the study summary (niche, tags, trend type), a reference toggle, a target character picker and remove.

## Tasks
1. **Migration 0006 + models:** manual_videos, reference_studies, finding_scores.tags / trend_type / audio_use.
2. **`tf_agent/manual.py`:**
   - add, with dedupe and role update;
   - background check (metadata + thumbnail);
   - set roles, which keeps the Target row in sync;
   - list with the study for a character;
   - remove.
3. **Schemas and prompts** (SeedStudy, AnalystResult); the orchestrator studies references from the list, cached and 2 in parallel; run inputs are saved into the list.
4. **API:** `GET/POST /api/manual-videos`, `PATCH/DELETE /api/manual-videos/{id}`; found_video carries tags / trend_type / audio_use.
5. **Frontend:** composer rename and saving; Library and Characters tabs; ManualCard; tags in VideoSheet.
6. **Tests, docs** (07, decisions D-51), commit.
