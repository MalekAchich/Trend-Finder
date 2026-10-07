"""One shape for a found video, shared by the live stream (`video.saved`) and the library API."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tf_db.models import CardFeedback, Finding, FindingScore, Video, VideoAnalysis


def thumb_url(path: str | None) -> str | None:
    return f"/api/media/thumbs/{Path(path).name}" if path else None


def found_video(finding: Finding, score: FindingScore | None, video: Video, analysis: VideoAnalysis | None,
                *, cluster_id: Any = None, feedback: CardFeedback | None = None) -> dict[str, Any]:
    sc = score
    best = (analysis.best_clean_segment if analysis else None) or None
    return {
        "id": str(finding.id),
        "cluster_id": str(cluster_id) if cluster_id else None,
        "run_id": str(finding.run_id),
        "canonical_id": video.canonical_id,
        "platform": video.platform,
        "platform_id": video.canonical_id.split(":", 1)[1],
        "url": video.url,
        "thumbnail_url": thumb_url(analysis.thumbnail_path if analysis else None),
        "creator": video.creator_handle,
        "caption": video.caption,
        "views": (video.metrics or {}).get("views"),
        "posted_at": video.posted_at.isoformat() if video.posted_at else None,
        "duration_s": video.duration_s,
        "score": sc.overall if sc else None,
        "scores": {"fit": sc.fit if sc else None, "feasibility": sc.feasibility if sc else None,
                   "momentum": sc.momentum if sc else None, "freshness": sc.freshness if sc else None},
        "why": (sc.fit_justification if sc else None) or finding.why,
        "adaptation": sc.adaptation_idea if sc else None,
        "best_segment": {"start_s": best["start_s"], "end_s": best["end_s"]} if best else None,
        "watch_out": sc.feasibility_notes if sc else None,
        "niche_guess": sc.niche_guess if sc else None,
        "tags": list(sc.tags or []) if sc else [],
        "trend_type": sc.trend_type if sc else None,
        "audio_use": sc.audio_use if sc else None,
        "source": finding.source,
        "found_at": finding.created_at.isoformat() if finding.created_at else None,
        "feedback": {"rating": feedback.rating, "note": feedback.note} if feedback else None,
    }
