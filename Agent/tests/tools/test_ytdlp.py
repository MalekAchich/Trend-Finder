import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tf_agent.tools.types import ToolFailure
from tf_agent.tools.ytdlp import YtDlp, classify_ytdlp_error, info_to_video_item

from . import samples

FIX = samples.ytdlp_info()


def test_tiktok_info_maps_to_video_item():
    v = info_to_video_item(FIX["tiktok"])
    assert v.canonical_id == "tiktok:1000000000000000021" and v.platform == "tiktok"
    assert v.creator.handle == "creator_e"
    assert v.metrics.model_dump() == {"views": 3731, "likes": 222, "comments": 5, "shares": 4, "saves": 9}
    assert v.posted_at == datetime.fromtimestamp(1762009942, UTC)
    assert v.duration_s == 8 and v.media_access == "ok" and v.source == "yt-dlp"
    assert v.caption.startswith("Test clip with a deadpan")


def test_youtube_info_maps_to_video_item():
    v = info_to_video_item(FIX["youtube"])
    assert v.canonical_id == "youtube:TestShort01" and v.platform == "youtube"
    assert v.creator.handle == "creator_f" and v.creator.followers == 18800
    assert v.metrics.views == 3933 and v.metrics.shares is None
    assert v.url == "https://www.youtube.com/shorts/TestShort01"


def test_hashtags_come_from_caption_and_tags():
    info = dict(FIX["tiktok"], description="Serious mood #DeadPan #dance_trend 🕺", tags=["Comedy"])
    assert info_to_video_item(info).hashtags == ["deadpan", "dance_trend", "comedy"]


@pytest.mark.parametrize("message,code", [
    ("ERROR: [Instagram] X: Requested content is not available, rate-limit reached or login required. "
     "Use --cookies-from-browser", "login_required"),
    ("ERROR: [TikTok] 123: Video not available, status code 10204", "not_found"),
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access", "not_found"),
    ("ERROR: Unable to download webpage: HTTP Error 429: Too Many Requests", "rate_limited"),
    ("ERROR: Unable to download webpage: <urlopen error timed out>", "platform_unavailable"),
])
def test_error_classification(message, code):
    assert classify_ytdlp_error(message) == code


async def test_metadata_timeout_is_platform_unavailable():
    def slow(url, opts, download):
        time.sleep(1.0)
        return FIX["tiktok"]

    started = time.monotonic()
    with pytest.raises(ToolFailure) as ei:
        await YtDlp(timeout_s=0.2, extractor=slow).metadata("https://www.tiktok.com/@a/video/1")
    assert ei.value.error.code == "platform_unavailable" and time.monotonic() - started < 0.8


async def test_metadata_maps_extractor_errors():
    from yt_dlp.utils import DownloadError

    def failing(url, opts, download):
        raise DownloadError("ERROR: [TikTok] 1: Video not available, status code 10204")

    with pytest.raises(ToolFailure) as ei:
        await YtDlp(extractor=failing).metadata("https://www.tiktok.com/@a/video/1")
    assert ei.value.error.code == "not_found"


async def test_parallelism_is_bounded():
    active = peak = 0

    def extract(url, opts, download):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        time.sleep(0.05)
        active -= 1
        return FIX["youtube"]

    y = YtDlp(max_parallel=2, extractor=extract)
    await asyncio.gather(*(y.metadata("https://youtu.be/TestShort01") for _ in range(6)))
    assert peak == 2


async def test_search_youtube_keeps_short_videos():
    def extract(url, opts, download):
        assert url == "ytsearch10:deadpan dance" and opts.get("extract_flat")
        return {"entries": [
            {"id": "aaaaaaaaaaa", "duration": 30, "view_count": 100, "title": "short", "channel": "c"},
            {"id": "bbbbbbbbbbb", "duration": 600, "view_count": 5, "title": "long"},
            {"id": "ccccccccccc", "duration": None, "title": "unknown length"}]}

    items = await YtDlp(extractor=extract).search_youtube("deadpan dance", n=10)
    assert [i.canonical_id for i in items] == ["youtube:aaaaaaaaaaa", "youtube:ccccccccccc"]
    assert items[0].url == "https://www.youtube.com/shorts/aaaaaaaaaaa" and items[0].metrics.views == 100


async def test_download_returns_file_path(tmp_path):
    def extract(url, opts, download):
        assert download and "height<=720" in opts["format"]
        target = tmp_path / "abc.mp4"
        target.write_bytes(b"video")
        return {"id": "abc", "requested_downloads": [{"filepath": str(target)}]}

    path = await YtDlp(extractor=extract).download("https://youtu.be/TestShort01", tmp_path)
    assert path == tmp_path / "abc.mp4" and path.read_bytes() == b"video"


@pytest.mark.live
async def test_live_shorts_metadata():
    v = await YtDlp().metadata("https://www.youtube.com/shorts/TestShort01")
    assert v.canonical_id == "youtube:TestShort01" and (v.metrics.views or 0) > 0



def test_instagram_creator_is_the_username_not_the_display_name():
    info = {"id": "TESTREEL002", "extractor_key": "Instagram", "webpage_url": "https://www.instagram.com/p/TESTREEL002/",
            "uploader": "Creator G Display", "channel": "creator_g", "duration": 10}
    assert info_to_video_item(info).creator.handle == "creator_g"


async def test_save_gives_the_video_with_sound_or_the_sound_alone(tmp_path):
    seen = []

    def extract(url, opts, download):
        seen.append(opts)
        out = Path(opts["outtmpl"].replace("%(id)s", "TestShort01").replace(".%(ext)s", ""))
        if opts.get("postprocessors"):
            out.with_suffix(".mp3").write_bytes(b"ID3")
        else:
            out.with_suffix(".mp4").write_bytes(b"\x00\x00\x00\x18ftyp")
        return {"id": "TestShort01"}

    converted = []

    async def convert(path):
        converted.append(path.name)
        return path

    y = YtDlp(extractor=extract, convert_video=convert)
    video = await y.save("https://youtu.be/TestShort01", tmp_path / "v", "video")
    sound = await y.save("https://youtu.be/TestShort01", tmp_path / "a", "audio")
    assert (video.name, sound.name) == ("TestShort01.mp4", "TestShort01.mp3")
    assert "+ba" in seen[0]["format"] and seen[0]["merge_output_format"] == "mp4"
    assert seen[1]["format"] == "ba/b" and seen[1]["postprocessors"][0]["preferredcodec"] == "mp3"
    assert converted == ["TestShort01.mp4"]  # only the video is made H.264


async def test_a_non_h264_video_is_re_encoded_once_and_h264_is_left_alone(tmp_path):
    import subprocess

    from tf_agent.tools.ytdlp import to_h264

    def clip(name, codec):
        out = tmp_path / name
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=64x112:rate=10:duration=1",
                        "-f", "lavfi", "-i", "sine=duration=1", "-c:v", codec, "-c:a", "aac", "-shortest", str(out)],
                       check=True)
        return out

    def codecs(path):
        return subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0",
                               str(path)], capture_output=True, text=True).stdout.split()

    old = await to_h264(clip("other.mkv", "mpeg4"))
    assert old.name == "other.mp4" and set(codecs(old)) == {"h264", "aac"} and not (tmp_path / "other.mkv").exists()
    fine = clip("fine.mp4", "libx264")
    before = fine.stat().st_mtime_ns
    assert await to_h264(fine) == fine and fine.stat().st_mtime_ns == before
