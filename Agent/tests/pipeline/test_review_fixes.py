"""Regression tests for the Plan 2 final review (pipeline)."""
import asyncio
import os
import shutil
from concurrent.futures.process import BrokenProcessPool

import pytest

from tf_agent.pipeline.analyze import VideoAnalyzer, thread_runner
from tf_agent.pipeline.pose import PoseAnalyzer
from tf_agent.pipeline.retention import MediaRetention
from tf_agent.pipeline.transcript import Transcriber
from tf_agent.tools.types import Metrics, VideoItem

from .conftest import ffmpeg


class Downloader:
    def __init__(self, source, delay=0.0):
        self.source, self.delay, self.calls = source, delay, 0

    async def download(self, url, dest_dir, max_height=720):
        self.calls += 1
        await asyncio.sleep(self.delay)
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / "clip.mp4"
        shutil.copy(self.source, target)
        return target


def item(duration=None, url="https://www.tiktok.com/@u/video/7000000000000000001"):
    return VideoItem(canonical_id="tiktok:7000000000000000001", platform="tiktok", url=url, duration_s=duration,
                     metrics=Metrics(views=1), media_access="ok")


@pytest.fixture(scope="module")
def runner():
    class SilentModel:
        def transcribe(self, audio, **kw):
            return iter(()), None

    r = thread_runner(PoseAnalyzer(), Transcriber(model_factory=SilentModel))
    yield r
    r.close()


def make(tmp_path, source, run, **kw):
    media = tmp_path / "media"
    return VideoAnalyzer(Downloader(source, kw.pop("delay", 0.0)), media_dir=media, heavy_runner=run,
                         retention=MediaRetention(media, 5 * 1024**3, protected=set), **kw)


@pytest.fixture(scope="module")
def odd_clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("odd")
    tiny, short_video, long_ = d / "tiny.mp4", d / "short_video_long_audio.mp4", d / "four_s.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=c=gray:size=270x480:rate=25:duration=0.04", "-c:v", "libx264", "-pix_fmt",
           "yuv420p", str(tiny))
    ffmpeg("-f", "lavfi", "-i", "color=c=gray:size=270x480:rate=25:duration=1", "-f", "lavfi", "-i",
           "sine=frequency=300:duration=6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(short_video))
    ffmpeg("-f", "lavfi", "-i", "color=c=gray:size=270x480:rate=25:duration=4", "-c:v", "libx264", "-pix_fmt",
           "yuv420p", str(long_))
    return {"tiny": tiny, "short_video": short_video, "four": long_}


async def test_tiny_clip_is_filtered_not_raised(odd_clips, tmp_path, runner):
    r = await make(tmp_path, odd_clips["tiny"], runner).analyze(item())
    assert r.filtered_reason is not None


async def test_video_shorter_than_audio_completes(odd_clips, tmp_path, runner):
    r = await make(tmp_path, odd_clips["short_video"], runner).analyze(item())
    assert r.filtered_reason in ("no_person", None) or r.filtered_reason.startswith("analysis_failed")
    assert r.contact_sheet_path is not None


async def test_long_videos_are_rejected_before_and_after_download(odd_clips, tmp_path, runner):
    a = make(tmp_path, odd_clips["four"], runner, max_duration_s=2.0)
    assert (await a.analyze(item(duration=1200))).filtered_reason == "too_long"
    assert a.downloader.calls == 0
    assert (await a.analyze(item())).filtered_reason == "too_long"


@pytest.mark.parametrize("error,reason", [(RuntimeError("boom"), "analysis_failed:heavy"),
                                          (BrokenProcessPool("dead"), "analysis_failed:heavy")])
async def test_heavy_failures_are_filtered(person_clips, tmp_path, error, reason):
    async def broken(job):
        raise error

    assert (await make(tmp_path, person_clips["one"], broken).analyze(item())).filtered_reason == reason


async def test_heavy_timeout_is_filtered(person_clips, tmp_path):
    async def hang(job):
        await asyncio.sleep(10)

    r = await make(tmp_path, person_clips["one"], hang, heavy_timeout_s=0.2).analyze(item())
    assert r.filtered_reason == "analysis_failed:heavy_timeout"


async def test_concurrent_analyses_of_one_video_share_the_work(person_clips, tmp_path, runner):
    a = make(tmp_path, person_clips["one"], runner, delay=0.1)
    first, second = await asyncio.gather(a.analyze(item()), a.analyze(item()))
    assert a.downloader.calls == 1 and first.feasibility == second.feasibility


async def test_url_must_match_canonical_id(person_clips, tmp_path, runner):
    bad = item(url="https://www.tiktok.com/@u/video/7999999999999999999")
    r = await make(tmp_path, person_clips["one"], runner).analyze(bad)
    assert r.filtered_reason == "invalid_item"


def test_retention_protects_directories_and_survives_vanishing_files(tmp_path):
    busy = tmp_path / "frames" / "busy"
    busy.mkdir(parents=True)
    (busy / "f.jpg").write_bytes(b"x" * 600)
    gone = tmp_path / "videos" / "old" / "v.mp4"
    gone.parent.mkdir(parents=True)
    gone.write_bytes(b"x" * 600)
    os.utime(gone, (1, 1))

    def protected():
        gone.unlink()  # another worker deletes it mid-scan
        return []

    r = MediaRetention(tmp_path, quota_bytes=500, protected=protected)
    r.enforce(extra_protected=[busy])
    assert (busy / "f.jpg").exists()
