"""Persistence for discovered videos and their analyses (`videos`, `video_analyses`)."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.pipeline.result import VideoAnalysisResult
from tf_agent.tools.types import Creator, Metrics, Sound, VideoItem
from tf_db.models import Video, VideoAnalysis


def _video_values(item: VideoItem) -> dict[str, Any]:
    has_metrics = any(v is not None for v in item.metrics.model_dump().values())
    values: dict[str, Any] = {
        "platform": item.platform, "url": item.url, "creator_handle": item.creator.handle,
        "creator_followers": item.creator.followers, "caption": item.caption, "hashtags": item.hashtags or None,
        "sound_id": item.sound.id, "sound_title": item.sound.title, "posted_at": item.posted_at,
        "duration_s": item.duration_s,
        "metrics": item.metrics.model_dump() if has_metrics else None,
        "metrics_at": datetime.now(UTC) if has_metrics else None,
        "media_access": item.media_access if item.media_access != "unknown" else None,
    }
    return {k: v for k, v in values.items() if v is not None}  # never erase known data with blanks


class VideoStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def upsert_videos(self, items: list[VideoItem]) -> None:
        async with self._sm() as s:
            for item in items:
                values = _video_values(item)
                insert_values = {"platform": item.platform, "url": item.url, **values}
                stmt = pg_insert(Video).values(canonical_id=item.canonical_id, **insert_values)
                stmt = stmt.on_conflict_do_update(index_elements=[Video.canonical_id], set_=values or
                                                  {"url": item.url})
                await s.execute(stmt)
            await s.commit()

    async def get_video(self, canonical_id: str) -> VideoItem | None:
        async with self._sm() as s:
            row = await s.get(Video, canonical_id)
        if row is None:
            return None
        return VideoItem(canonical_id=row.canonical_id, platform=row.platform, url=row.url,
                         creator=Creator(handle=row.creator_handle, followers=row.creator_followers),
                         caption=row.caption, hashtags=list(row.hashtags or []),
                         sound=Sound(id=row.sound_id, title=row.sound_title), posted_at=row.posted_at,
                         duration_s=row.duration_s, metrics=Metrics(**(row.metrics or {})),
                         media_access=row.media_access, source="yt-dlp" if row.metrics else "searxng")

    async def get_analysis(self, canonical_id: str, version: str) -> VideoAnalysisResult | None:
        async with self._sm() as s:
            row = (await s.execute(select(VideoAnalysis).where(VideoAnalysis.canonical_id == canonical_id,
                                                               VideoAnalysis.pipeline_version == version))
                   ).scalar_one_or_none()
        if row is None:
            return None
        return VideoAnalysisResult(
            canonical_id=row.canonical_id, pipeline_version=row.pipeline_version, probe=row.probe,
            cuts=list(row.cuts or []), cut_rate=row.cut_rate, transcript=row.transcript, pose=row.pose,
            camera_motion=row.camera_motion, best_clean_segment=row.best_clean_segment, feasibility=row.feasibility,
            filtered_reason=row.filtered_reason, fingerprint=row.fingerprint,
            contact_sheet_path=row.contact_sheet_path, media_path=row.media_path)

    async def save_analysis(self, result: VideoAnalysisResult) -> None:
        values = result.as_dict()
        stmt = pg_insert(VideoAnalysis).values(**values)
        stmt = stmt.on_conflict_do_update(constraint="uq_video_analyses_canonical_id",
                                          set_={k: v for k, v in values.items()
                                                if k not in ("canonical_id", "pipeline_version")})
        async with self._sm() as s:
            await s.execute(stmt)
            await s.commit()
