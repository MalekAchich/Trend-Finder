import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from tf_agent.pipeline.fingerprint import frame_hashes, hash_distance
from tf_agent.pipeline.frames import sample_frames
from tf_agent.pipeline.retention import MediaRetention
from tf_agent.pipeline.transcript import Transcriber


@dataclass
class Seg:
    start: float
    end: float
    text: str


@dataclass
class Info:
    language: str


class FakeWhisper:
    def __init__(self, segments, language="en"):
        self.segments, self.language, self.calls = segments, language, []

    def transcribe(self, path, **kw):
        self.calls.append((path, kw))
        return iter(self.segments), Info(self.language)


def test_no_audio_never_loads_the_model(tmp_path):
    def boom():
        raise AssertionError("model must not load for silent videos")

    t = Transcriber(model_factory=boom).transcribe(tmp_path / "x.mp4", has_audio=False, duration_s=5)
    assert (t.language, t.text, t.speech_ratio, t.segments) == (None, "", 0.0, [])


def test_transcript_text_language_and_speech_ratio(clips):
    fake = FakeWhisper([Seg(0.0, 1.0, " hi "), Seg(2.0, 2.5, "there")])
    t = Transcriber(model_factory=lambda: fake).transcribe(clips["av"], has_audio=True, duration_s=3.0)
    assert t.text == "hi there" and t.language == "en" and t.speech_ratio == pytest.approx(0.5)
    assert t.segments == [{"start": 0.0, "end": 1.0, "text": "hi"}, {"start": 2.0, "end": 2.5, "text": "there"}]
    assert fake.calls[0][1]["vad_filter"] is True


def test_music_only_has_no_language(clips):
    t = Transcriber(model_factory=lambda: FakeWhisper([])).transcribe(clips["av"], has_audio=True, duration_s=3)
    assert t.language is None and t.speech_ratio == 0.0


def test_model_is_loaded_once(clips):
    loads = []

    def factory():
        loads.append(1)
        return FakeWhisper([])

    tr = Transcriber(model_factory=factory)
    for _ in range(3):
        tr.transcribe(clips["av"], has_audio=True, duration_s=3)
    assert len(loads) == 1


async def test_frame_hash_distance(clips, tmp_path):
    a = frame_hashes([p for _, p in await sample_frames(clips["silent"], tmp_path / "a", fps=2)])
    b = frame_hashes([p for _, p in await sample_frames(clips["static"], tmp_path / "b", fps=2)])
    assert hash_distance(a, a) == 0
    assert hash_distance(a, b) > 10
    assert hash_distance(a, []) == 64.0


def make_file(path: Path, size: int, mtime: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))
    return path


def test_retention_evicts_oldest_unprotected_media(tmp_path):
    old = make_file(tmp_path / "videos" / "old.mp4", 400, 1000)
    keep = make_file(tmp_path / "videos" / "keep.mp4", 400, 900)
    new = make_file(tmp_path / "videos" / "new.mp4", 400, 3000)
    frames = make_file(tmp_path / "frames" / "x" / "f.jpg", 100, 500)
    sheet = make_file(tmp_path / "sheets" / "s.jpg", 100, 100)
    r = MediaRetention(tmp_path, quota_bytes=1000, protected=lambda: {keep.resolve()})
    assert r.usage() == 1400
    r.enforce()
    assert keep.exists() and new.exists() and sheet.exists()
    assert not frames.exists() and not old.exists()
    assert r.usage() <= 1000


def test_can_download_stops_at_95_percent(tmp_path):
    make_file(tmp_path / "sheets" / "s.jpg", 940, 1)
    r = MediaRetention(tmp_path, quota_bytes=1000, protected=set)
    assert r.can_download()
    make_file(tmp_path / "sheets" / "t.jpg", 20, 1)
    assert not r.can_download()


def test_audio_is_decoded_with_ffmpeg_not_pyav(clips):
    import numpy as np

    fake = FakeWhisper([Seg(0.0, 1.0, "tone")])
    Transcriber(model_factory=lambda: fake).transcribe(clips["av"], has_audio=True, duration_s=3.0)
    audio = fake.calls[0][0]
    assert isinstance(audio, np.ndarray) and audio.dtype == np.float32
    assert abs(len(audio) - 3 * 16000) < 1600 and 0.05 < float(np.abs(audio).max()) <= 1.0


def test_undecodable_audio_means_no_speech(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"garbage")
    fake = FakeWhisper([Seg(0.0, 1.0, "never")])
    assert Transcriber(model_factory=lambda: fake).transcribe(bad, has_audio=True, duration_s=3).text == ""
    assert fake.calls == []
