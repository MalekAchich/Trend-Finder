"""Plan 5 Task 3: keep a small thumbnail, never the video; contact sheets live until the run is curated."""
import uuid
from pathlib import Path

from PIL import Image
from sqlalchemy import select

from tf_agent.pipeline import PIPELINE_VERSION
from tf_agent.pipeline.media import cleanup_run_media
from tf_agent.tools.store import VideoStore
from tf_db.models import Finding, VideoAnalysis

from .test_analyze import analyzer, item, runner  # noqa: F401 (runner is a fixture)


async def test_thumbnail_kept_video_deleted(person_clips, tmp_path, runner):
    r = await analyzer(tmp_path, person_clips["one"], runner).analyze(item())
    thumb = Path(r.thumbnail_path)
    assert thumb.parent.name == "thumbs" and thumb.stat().st_size <= 40_000
    with Image.open(thumb) as im:
        assert im.width == 360 and im.format == "JPEG"
    assert r.media_path is None and not any((tmp_path / "media" / "videos").rglob("*.mp4"))
    assert Path(r.contact_sheet_path).exists()


async def test_video_deleted_when_analysis_fails(tmp_path, runner):
    r = await analyzer(tmp_path, "garbage", runner).analyze(item())
    assert r.filtered_reason == "probe_failed" and not any((tmp_path / "media" / "videos").rglob("*"))


async def test_missing_sheet_triggers_reanalysis(person_clips, tmp_path, runner, db_sessionmaker):
    """Review Focus 2: a cached analysis whose sheet was cleaned up is redone before the analyst sees it."""
    store = VideoStore(db_sessionmaker)
    a = analyzer(tmp_path, person_clips["one"], runner, store=store)
    first = await a.analyze(item())
    Path(first.contact_sheet_path).unlink()
    second = await a.analyze(item())
    assert a.downloader.calls == 2 and Path(second.contact_sheet_path).exists()


async def test_cleanup_deletes_only_this_runs_sheets(tmp_path, db_sessionmaker):
    from tf_agent.testing import create_test_run
    from tf_db.models import Video

    _, run_a, _ = await create_test_run(db_sessionmaker, n_tasks=0)
    _, run_b, _ = await create_test_run(db_sessionmaker, n_tasks=0, slug="other")
    sheets = {}
    async with db_sessionmaker() as s:
        for cid, run in (("tiktok:1", run_a), ("tiktok:2", run_b)):
            sheet = tmp_path / f"{cid.replace(':', '_')}.jpg"
            sheet.write_bytes(b"jpg")
            sheets[cid] = sheet
            s.add(Video(canonical_id=cid, platform="tiktok", url=f"https://www.tiktok.com/@u/video/{cid[7:]}"))
            await s.flush()
            s.add(VideoAnalysis(canonical_id=cid, pipeline_version=PIPELINE_VERSION, contact_sheet_path=str(sheet)))
            s.add(Finding(run_id=run, canonical_id=cid))
        await s.commit()
    removed = await cleanup_run_media(db_sessionmaker, run_a)
    assert removed == 1 and not sheets["tiktok:1"].exists() and sheets["tiktok:2"].exists()
    async with db_sessionmaker() as s:
        paths = dict((await s.execute(select(VideoAnalysis.canonical_id, VideoAnalysis.contact_sheet_path))).all())
    assert paths == {"tiktok:1": None, "tiktok:2": str(sheets["tiktok:2"])}
