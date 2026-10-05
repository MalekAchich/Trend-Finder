"""Local speech transcription with faster-whisper (CPU int8, VAD) — loaded lazily, once per process."""
from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

SAMPLE_RATE = 16000


def decode_audio(path: Path) -> np.ndarray:
    """16 kHz mono float32 via ffmpeg (avoids faster-whisper's PyAV decoder, which breaks across PyAV versions)."""
    out = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar",
                          str(SAMPLE_RATE), "-f", "s16le", "-"], capture_output=True, timeout=300, check=True).stdout
    return np.frombuffer(out, dtype=np.int16).astype(np.float32) / 32768.0


@dataclass
class Transcript:
    language: str | None = None
    text: str = ""
    speech_ratio: float = 0.0
    segments: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class Transcriber:
    def __init__(self, model_size: str = "small", compute_type: str = "int8",
                 model_factory: Callable[[], Any] | None = None) -> None:
        self.model_size = model_size
        self.compute_type = compute_type
        self._factory = model_factory
        self._model: Any = None

    def _get(self) -> Any:
        if self._model is None:
            if self._factory is not None:
                self._model = self._factory()
            else:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(self.model_size, device="cpu", compute_type=self.compute_type)
        return self._model

    def transcribe(self, path: Path, has_audio: bool, duration_s: float) -> Transcript:
        if not has_audio:
            return Transcript()
        try:
            audio = decode_audio(path)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return Transcript()  # undecodable audio track: treat as no speech
        if audio.size == 0:
            return Transcript()
        segments, info = self._get().transcribe(audio, vad_filter=True, beam_size=1)
        segs = [{"start": round(float(s.start), 2), "end": round(float(s.end), 2), "text": s.text.strip()}
                for s in segments if s.text.strip()]
        if not segs:
            return Transcript()
        speech = sum(s["end"] - s["start"] for s in segs)
        return Transcript(language=getattr(info, "language", None), text=" ".join(s["text"] for s in segs),
                          speech_ratio=round(min(speech / duration_s, 1.0), 3) if duration_s > 0 else 0.0,
                          segments=segs)
