"""Kling feasibility score and hard filter (04-video-pipeline.md, formula verbatim)."""
from __future__ import annotations

from tf_agent.pipeline.pose import PoseStats

MIN_SINGLE_PERSON = 0.5
MIN_BODY_VISIBILITY = 0.4
MAX_CLEAN_MOTION = 0.5
# No clean stretch (cuts, handheld camera): harder for motion transfer, but edited memes and handheld vlogs are often
# exactly the trend, so it costs score instead of rejecting the video (run 1 lost 17 videos to it, 5 of them the
# owner's own targets).
NO_CLEAN_SEGMENT_FACTOR = 0.75


def clean_segments(times: list[float], single: list[bool], cuts: list[float], motion: list[float],
                   min_len: float = 3.0, max_motion: float = MAX_CLEAN_MOTION) -> list[tuple[float, float]]:
    """Windows with exactly one person, no cut and a steady camera, longest first."""
    if not times:
        return []
    dt = times[1] - times[0] if len(times) > 1 else 0.5
    segments: list[tuple[float, float]] = []
    start: float | None = None
    prev_t: float | None = None
    for t, ok, m in zip(times, single, motion, strict=True):
        cut_between = prev_t is not None and any(prev_t < c <= t for c in cuts)
        clean = ok and m <= max_motion
        if start is not None and (not clean or cut_between):
            end = min(c for c in cuts if prev_t < c <= t) if cut_between else t  # segment ends at the cut/bad frame
            segments.append((start, end))
            start = None
        if clean and start is None:
            start = t
        prev_t = t
    if start is not None:
        segments.append((start, times[-1] + dt))
    kept = [(round(a, 3), round(b, 3)) for a, b in segments if b - a >= min_len - 1e-9]
    return sorted(kept, key=lambda s: s[1] - s[0], reverse=True)


def feasibility(stats: PoseStats, camera_motion: float, cut_rate: float,
                best_segment: tuple[float, float] | None) -> tuple[float, str | None]:
    score = 100 * (0.30 * stats.single_person_ratio
                   + 0.30 * stats.body_visibility
                   + 0.10 * stats.hands_visibility
                   + 0.10 * stats.face_size_ok
                   + 0.10 * (1 - camera_motion)
                   + 0.10 * (1 - min(cut_rate / 3, 1)))
    reason = None
    if stats.single_person_ratio < MIN_SINGLE_PERSON:
        reason = "no_person" if max(stats.per_frame_people, default=0) == 0 else "multiple_people"
    elif stats.body_visibility < MIN_BODY_VISIBILITY:
        reason = "body_not_visible"
    if best_segment is None:
        score *= NO_CLEAN_SEGMENT_FACTOR
    return round(score, 2), reason
