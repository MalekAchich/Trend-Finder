"""What we keep of a video: one small thumbnail. Videos go right after analysis, contact sheets after curation."""
from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.pipeline.ffmpeg import run
from tf_db.models import Finding, VideoAnalysis

THUMB_WIDTH = 360
THUMB_MAX_BYTES = 40_000


async def make_thumbnail(video: Path, out_path: Path, at_s: float) -> Path:
    """One JPEG frame, 360 px wide, re-encoded at lower quality until it fits 40 KB."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp.jpg")
    for q in (4, 7, 10, 14, 20, 28):
        await run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", f"{max(at_s, 0):.2f}", "-i", str(video),
                   "-frames:v", "1", "-vf", f"scale={THUMB_WIDTH}:-2", "-q:v", str(q), str(tmp)], timeout_s=60)
        if tmp.stat().st_size <= THUMB_MAX_BYTES:
            break
    tmp.replace(out_path)
    return out_path


async def cleanup_run_media(sessionmaker: async_sessionmaker[AsyncSession], run_id: uuid.UUID) -> int:
    """Delete the contact sheets of this run's findings (cross-check is done) and forget their paths."""
    async with sessionmaker() as s:
        rows = (await s.execute(select(VideoAnalysis.id, VideoAnalysis.contact_sheet_path)
                                .join(Finding, Finding.canonical_id == VideoAnalysis.canonical_id)
                                .where(Finding.run_id == run_id, VideoAnalysis.contact_sheet_path.is_not(None)))
                ).all()
        removed = 0
        for _, path in rows:
            p = Path(path)
            if p.exists():
                p.unlink(missing_ok=True)
                removed += 1
        if rows:
            await s.execute(update(VideoAnalysis).where(VideoAnalysis.id.in_([i for i, _ in rows]))
                            .values(contact_sheet_path=None))
            await s.commit()
    return removed
