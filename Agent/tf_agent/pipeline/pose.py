"""MediaPipe pose statistics per video: people count, body/hand visibility, face size (04-video-pipeline.md)."""
from __future__ import annotations

import os
import statistics
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/"
             "pose_landmarker_lite.task")
DEFAULT_MODEL = Path.home() / ".cache" / "trendfinder" / "models" / "pose_landmarker_lite.task"
KEY_LANDMARKS = (0, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28)  # head, shoulders, elbows, wrists, hips, knees, ankles
WRISTS = (15, 16)
VISIBLE = 0.5
FACE_MIN_FRACTION = 0.08


def ensure_model(path: Path = DEFAULT_MODEL) -> Path:
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        r = client.get(MODEL_URL)
        r.raise_for_status()
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    with os.fdopen(fd, "wb") as f:
        f.write(r.content)
    os.replace(tmp, path)
    return path


@dataclass
class PoseStats:
    per_frame_people: list[int] = field(default_factory=list)
    per_frame_single: list[bool] = field(default_factory=list)
    single_person_ratio: float = 0.0
    body_visibility: float = 0.0
    hands_visibility: float = 0.0
    face_size_ok: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


def _visible(lm) -> bool:
    return lm.visibility > VISIBLE and 0.0 <= lm.x <= 1.0 and 0.0 <= lm.y <= 1.0


class PoseAnalyzer:
    def __init__(self, model_path: Path | None = None, num_poses: int = 3) -> None:
        self.model_path = model_path
        self.num_poses = num_poses
        self._landmarker = None

    def close(self) -> None:
        """Release the MediaPipe landmarker. Must be called by owners: MediaPipe deadlocks when its __del__
        runs late during interpreter shutdown."""
        if self._landmarker is not None:
            landmarker, self._landmarker = self._landmarker, None
            landmarker.close()

    def __enter__(self) -> PoseAnalyzer:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get(self):
        if self._landmarker is None:
            from mediapipe.tasks.python.core.base_options import BaseOptions
            from mediapipe.tasks.python.vision import PoseLandmarker, PoseLandmarkerOptions

            model = ensure_model(self.model_path or DEFAULT_MODEL)
            self._landmarker = PoseLandmarker.create_from_options(PoseLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(model)), num_poses=self.num_poses))
        return self._landmarker

    def analyze(self, frames: list[Path]) -> PoseStats:
        from mediapipe.tasks.python.vision.core.image import Image as MPImage
        from mediapipe.tasks.python.vision.core.image import ImageFormat

        landmarker = self._get()
        people, single, body_vis, hands, heads = [], [], [], [], []
        for path in frames:
            with Image.open(path) as im:
                arr = np.ascontiguousarray(np.asarray(im.convert("RGB")))
            poses = landmarker.detect(MPImage(image_format=ImageFormat.SRGB, data=arr)).pose_landmarks
            people.append(len(poses))
            single.append(len(poses) == 1)
            if len(poses) != 1:
                continue
            lms = poses[0]
            body_vis.append(sum(_visible(lms[i]) for i in KEY_LANDMARKS) / len(KEY_LANDMARKS))
            hands.append(all(_visible(lms[i]) for i in WRISTS))
            shoulder_y = (lms[11].y + lms[12].y) / 2
            heads.append(abs(shoulder_y - lms[0].y) * 2.2)  # nose→shoulders ≈ half a head box
        n = len(frames) or 1
        median_head = statistics.median(heads) if heads else 0.0
        return PoseStats(
            per_frame_people=people, per_frame_single=single,
            single_person_ratio=sum(single) / n,
            body_visibility=statistics.fmean(body_vis) if body_vis else 0.0,
            hands_visibility=(sum(hands) / len(hands)) if hands else 0.0,
            face_size_ok=min(median_head / FACE_MIN_FRACTION, 1.0) if heads else 0.0,
        )
