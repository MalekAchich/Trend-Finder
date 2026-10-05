import pytest
from PIL import Image

from tf_agent.pipeline.ffmpeg import PipelineError, probe, scene_cuts
from tf_agent.pipeline.frames import key_frames, sample_frames
from tf_agent.pipeline.sheet import make_contact_sheet


async def test_probe_reports_video_and_audio(clips):
    p = await probe(clips["av"])
    assert (p.width, p.height) == (720, 1280) and p.fps == pytest.approx(30, abs=0.1)
    assert p.duration_s == pytest.approx(3.0, abs=0.15) and p.has_audio is True
    assert (await probe(clips["silent"])).has_audio is False


async def test_scene_cut_detected_at_color_change(clips):
    cuts = await scene_cuts(clips["cut"])
    assert len(cuts) == 1 and cuts[0] == pytest.approx(1.5, abs=0.2)
    assert await scene_cuts(clips["static"]) == []


async def test_sample_frames_rate_and_size(clips, tmp_path):
    frames = await sample_frames(clips["silent"], tmp_path / "f", fps=2, width=360)
    assert 5 <= len(frames) <= 7
    t0, first = frames[0]
    assert t0 == 0.0 and Image.open(first).size[0] == 360
    assert [t for t, _ in frames] == sorted(t for t, _ in frames)


async def test_key_frames_spread_and_avoid_cuts(clips, tmp_path):
    frames = await key_frames(clips["cut"], tmp_path / "k", duration_s=3.0, cuts=[1.5], n=12)
    times = [t for t, _ in frames]
    assert len(frames) == 12 and times == sorted(times)
    assert all(abs(t - 1.5) >= 0.15 for t in times)
    assert all(p.exists() for _, p in frames)


async def test_contact_sheet_is_a_3x4_grid(clips, tmp_path):
    frames = await key_frames(clips["silent"], tmp_path / "k", duration_s=3.0, cuts=[], n=12)
    sheet = make_contact_sheet(frames, tmp_path / "sheet.jpg")
    img = Image.open(sheet)
    cell_w = 1024 // 3
    fw, fh = Image.open(frames[0][1]).size  # key frames are scaled to even dimensions (480x854)
    assert img.size == (cell_w * 3, round(cell_w * fh / fw) * 4)


async def test_corrupt_or_empty_files_raise(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video at all")
    with pytest.raises(PipelineError):
        await probe(bad)
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(PipelineError):
        await probe(empty)
