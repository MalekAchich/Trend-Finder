"""The owner's downloads: each file fetched once, never half-served, old copies cleaned up."""
import asyncio
import os

import pytest

from tf_agent.tools.types import ToolFailure
from tf_backend.downloads import KEEP_S, Downloads


def fake_save(calls, fail=False):
    async def save(url, folder, kind):
        calls.append((url, kind))
        await asyncio.sleep(0.01)
        folder.mkdir(parents=True, exist_ok=True)
        if fail:
            (folder / "half.mp4.part").write_bytes(b"x")
            raise ToolFailure("login_required", "log in to see this")
        f = folder / ("TestShort01.mp3" if kind == "audio" else "TestShort01.mp4")
        f.write_bytes(b"data")
        return f
    return save


async def test_two_clicks_download_once_and_the_copy_is_reused(tmp_path):
    calls = []
    d = Downloads(fake_save(calls), tmp_path)
    a, b = await asyncio.gather(d.get("youtube:TestShort01", "https://www.youtube.com/shorts/TestShort01", "video"),
                                d.get("youtube:TestShort01", "https://www.youtube.com/shorts/TestShort01", "video"))
    again = await d.get("youtube:TestShort01", "https://www.youtube.com/shorts/TestShort01", "video")
    sound = await d.get("youtube:TestShort01", "https://www.youtube.com/shorts/TestShort01", "audio")
    assert a == b == again and a.read_bytes() == b"data" and sound.suffix == ".mp3"
    assert [k for _, k in calls] == ["video", "audio"]


async def test_a_failed_download_leaves_nothing_behind(tmp_path):
    d = Downloads(fake_save([], fail=True), tmp_path)
    with pytest.raises(ToolFailure):
        await d.get("youtube:TestShort01", "https://www.youtube.com/shorts/TestShort01", "video")
    assert not any(p.is_file() for p in tmp_path.rglob("*"))


async def test_old_copies_are_cleaned_up(tmp_path):
    now = [10_000_000.0]
    d = Downloads(fake_save([]), tmp_path, clock=lambda: now[0])
    old = await d.get("youtube:TestShort01", "https://www.youtube.com/shorts/TestShort01", "video")
    os.utime(old.parent, (now[0] - KEEP_S - 1, now[0] - KEEP_S - 1))
    await d.get("youtube:TestShort02", "https://www.youtube.com/shorts/TestShort02", "video")
    assert not old.exists()
