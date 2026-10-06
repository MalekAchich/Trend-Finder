"""The persisted outcome of analysing one video (mirrors the `video_analyses` table)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from tf_agent.pipeline import PIPELINE_VERSION


@dataclass
class VideoAnalysisResult:
    canonical_id: str
    pipeline_version: str = PIPELINE_VERSION
    probe: dict[str, Any] | None = None
    cuts: list[float] = field(default_factory=list)
    cut_rate: float | None = None
    transcript: dict[str, Any] | None = None
    pose: dict[str, Any] | None = None
    camera_motion: float | None = None
    best_clean_segment: dict[str, float] | None = None
    feasibility: float | None = None
    filtered_reason: str | None = None
    fingerprint: dict[str, Any] | None = None
    contact_sheet_path: str | None = None
    thumbnail_path: str | None = None
    media_path: str | None = None

    @classmethod
    def filtered(cls, canonical_id: str, reason: str, **kw: Any) -> VideoAnalysisResult:
        return cls(canonical_id=canonical_id, filtered_reason=reason, **kw)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
