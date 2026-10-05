import shutil

import pytest

from tf_agent.pipeline import PIPELINE_VERSION
from tf_agent.pipeline.analyze import VideoAnalyzer, process_pool_runner, thread_runner
from tf_agent.pipeline.pose import PoseAnalyzer
from tf_agent.pipeline.retention import MediaRetention
from tf_agent.pipeline.transcript import Transcriber
from tf_agent.tools.store import VideoStore
from tf_agent.tools.types import Metrics, ToolFailure, VideoItem


class FakeDownloader:
    def __init__(self, source):
        self.source, self.calls = source, 0

    async def download(self, url, dest_dir, max_height=720):
        self.calls += 1
        if isinstance(self.source, Exception):
            raise self.source
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / "clip.mp4"
        if self.source == "garbage":
            target.write_bytes(b"not a video")
        else:
            shutil.copy(self.source, target)
        return target


def item(cid="tiktok:7000000000000000001", access="ok"):
    from tf_agent.tools.normalize import canonical_url

    url = canonical_url(cid, f"https://www.tiktok.com/@u/video/{cid.split(':')[1]}")
    return VideoItem(canonical_id=cid, platform=cid.split(":")[0], url=url,
                     metrics=Metrics(views=10), media_access=access)


@pytest.fixture(scope="module")
def runner():
    r = thread_runner(PoseAnalyzer(), Transcriber(model_factory=lambda: (_ for _ in ()).throw(AssertionError())))
    yield r
    r.close()


def analyzer(tmp_path, source, runner, store=None):
    media = tmp_path / "media"
    return VideoAnalyzer(FakeDownloader(source), media_dir=media, heavy_runner=runner, store=store,
                         retention=MediaRetention(media, 5 * 1024**3, protected=set))


async def test_full_analysis_of_one_person_video(person_clips, tmp_path, runner):
    a = analyzer(tmp_path, person_clips["one"], runner)
    r = await a.analyze(item())
    assert r.filtered_reason is None and r.feasibility >= 60
    assert r.pipeline_version == PIPELINE_VERSION and r.probe["duration_s"] == pytest.approx(3.0, abs=0.2)
    assert r.best_clean_segment["end_s"] - r.best_clean_segment["start_s"] >= 3.0
    assert r.transcript["text"] == "" and len(r.fingerprint["frame_hashes"]) == 12
    sheet = tmp_path / "media" / "sheets" / "tiktok_7000000000000000001.jpg"
    assert r.contact_sheet_path == str(sheet) and sheet.exists()
    assert not any((tmp_path / "media" / "frames").glob("*/*.jpg"))


async def test_two_people_are_filtered(person_clips, tmp_path, runner):
    r = await analyzer(tmp_path, person_clips["two"], runner).analyze(item())
    assert r.filtered_reason == "multiple_people"


async def test_login_required_is_filtered_without_download(tmp_path, runner):
    a = analyzer(tmp_path, "unused", runner)
    r = await a.analyze(item("instagram:DTfu8CIDezV", access="login_required"))
    assert r.filtered_reason == "media_unavailable" and a.downloader.calls == 0


async def test_corrupt_download_is_filtered(tmp_path, runner):
    r = await analyzer(tmp_path, "garbage", runner).analyze(item())
    assert r.filtered_reason == "probe_failed"


async def test_transient_download_failure_is_not_cached(tmp_path, runner, db_sessionmaker):
    store = VideoStore(db_sessionmaker)
    a = analyzer(tmp_path, ToolFailure("platform_unavailable", "down"), runner, store=store)
    r = await a.analyze(item())
    assert r.filtered_reason == "download_failed:platform_unavailable"
    assert await store.get_analysis("tiktok:7000000000000000001", PIPELINE_VERSION) is None


async def test_analysis_is_persisted_and_idempotent(person_clips, tmp_path, runner, db_sessionmaker):
    store = VideoStore(db_sessionmaker)
    a = analyzer(tmp_path, person_clips["one"], runner, store=store)
    first = await a.analyze(item())
    second = await a.analyze(item())
    assert a.downloader.calls == 1 and second.feasibility == first.feasibility
    stored = await store.get_analysis("tiktok:7000000000000000001", PIPELINE_VERSION)
    assert stored.contact_sheet_path == first.contact_sheet_path


async def test_process_pool_runner_matches_thread_runner(person_clips, tmp_path):
    pool = process_pool_runner(max_workers=1)
    try:
        r = await analyzer(tmp_path, person_clips["one"], pool).analyze(item())
    finally:
        pool.close()
    assert r.filtered_reason is None and r.feasibility >= 60
