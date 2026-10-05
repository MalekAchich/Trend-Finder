"""Global camera motion from dense optical flow between consecutive analysis frames (0 = static camera)."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

FLOW_WIDTH = 160
FULL_SCALE_PX = 4.0  # median shift (px at 160 px width, 0.5 s apart) treated as "fully moving"


def _gray(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"unreadable frame {path}")
    h, w = img.shape
    return cv2.resize(img, (FLOW_WIDTH, max(1, round(h * FLOW_WIDTH / w))))


def camera_motion(frames: list[Path]) -> tuple[float, list[float]]:
    """Return (mean motion 0–1, per-frame motion aligned to `frames`; the first frame is 0)."""
    if len(frames) < 2:
        return 0.0, [0.0] * len(frames)
    per_frame = [0.0]
    prev = _gray(frames[0])
    for path in frames[1:]:
        cur = _gray(path)
        if cur.shape != prev.shape:
            cur = cv2.resize(cur, (prev.shape[1], prev.shape[0]))
        flow = cv2.calcOpticalFlowFarneback(prev, cur, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        magnitude = np.linalg.norm(flow, axis=2)
        per_frame.append(float(min(np.median(magnitude) / FULL_SCALE_PX, 1.0)))  # median ignores a moving subject
        prev = cur
    return float(np.mean(per_frame[1:])), per_frame
