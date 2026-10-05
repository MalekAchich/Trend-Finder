"""yt-dlp adapter: metadata, ≤720p downloads and YouTube search for any platform URL (03-tools.md: get_video)."""
from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tf_agent.tools.normalize import canonical_id, norm_handle, norm_hashtag
from tf_agent.tools.types import Creator, Metrics, Sound, ToolErrorCode, ToolFailure, VideoItem

Extractor = Callable[[str, dict[str, Any], bool], dict[str, Any]]
_HASHTAG_RE = re.compile(r"#(\w+)", re.UNICODE)
DOWNLOAD_FORMAT = ("bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/bv*[height<=720]+ba/"
                   "b[height<=720]/b")


def _default_extract(url: str, opts: dict[str, Any], download: bool) -> dict[str, Any]:
    import yt_dlp

    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=download)


def classify_ytdlp_error(message: str) -> ToolErrorCode:
    m = message.lower()
    if "login required" in m or "cookies" in m or "log in" in m:
        return "login_required"
    if "429" in m or "too many requests" in m:
        return "rate_limited"
    if any(k in m for k in ("not available", "private video", "removed", "does not exist", "404", "unavailable")):
        return "not_found"
    return "platform_unavailable"


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def info_to_video_item(info: dict[str, Any]) -> VideoItem:
    extractor = (info.get("extractor_key") or info.get("ie_key") or "").lower()
    url = info.get("webpage_url") or info.get("url") or ""
    cid = canonical_id(url)
    if cid is None and "youtube" in extractor and info.get("id"):
        cid = f"youtube:{info['id']}"
    if cid is None and "tiktok" in extractor and info.get("id"):
        cid = f"tiktok:{info['id']}"
    if cid is None and "instagram" in extractor and info.get("id"):
        cid = f"instagram:{info['id']}"
    if cid is None:
        raise ToolFailure("invalid_input", f"unsupported video URL: {url}")
    platform, vid = cid.split(":", 1)
    if platform == "youtube":
        url = f"https://www.youtube.com/shorts/{vid}"
    caption = info.get("description") or info.get("title") or None
    tags: list[str] = []
    for t in _HASHTAG_RE.findall(caption or "") + list(info.get("tags") or []):
        n = norm_hashtag(str(t))
        if n and n not in tags:
            tags.append(n)
    handle = info.get("uploader_id") if platform == "youtube" else info.get("uploader") or info.get("uploader_id")
    ts = info.get("timestamp")
    track = (info.get("track") or "").strip() or None
    return VideoItem(
        canonical_id=cid, platform=platform, url=url,
        creator=Creator(handle=norm_handle(str(handle)) if handle else None,
                        followers=_int(info.get("channel_follower_count"))),
        caption=caption, hashtags=tags,
        sound=Sound(id=info.get("track_id") or None, title=track),
        posted_at=datetime.fromtimestamp(ts, UTC) if ts else None,
        duration_s=float(info["duration"]) if info.get("duration") is not None else None,
        metrics=Metrics(views=_int(info.get("view_count")), likes=_int(info.get("like_count")),
                        comments=_int(info.get("comment_count")), shares=_int(info.get("repost_count")),
                        saves=_int(info.get("save_count"))),
        thumbnail_url=info.get("thumbnail"), media_access="ok", source="yt-dlp",
    )


class YtDlp:
    def __init__(self, timeout_s: float = 45.0, max_parallel: int = 2, cookies_file: Path | None = None,
                 extractor: Extractor | None = None) -> None:
        self.timeout_s = timeout_s
        self.cookies_file = cookies_file
        self._extract = extractor or _default_extract
        self._slots = asyncio.Semaphore(max_parallel)

    def _opts(self, **extra: Any) -> dict[str, Any]:
        opts: dict[str, Any] = {"quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 20}
        if self.cookies_file:
            opts["cookiefile"] = str(self.cookies_file)
        opts.update(extra)
        return opts

    async def _run(self, url: str, opts: dict[str, Any], download: bool, timeout_s: float) -> dict[str, Any]:
        async with self._slots:
            try:
                return await asyncio.wait_for(asyncio.to_thread(self._extract, url, opts, download), timeout_s)
            except TimeoutError:
                raise ToolFailure("platform_unavailable", f"yt-dlp timed out after {timeout_s}s") from None
            except ToolFailure:
                raise
            except Exception as e:  # yt_dlp.utils.DownloadError and friends
                raise ToolFailure(classify_ytdlp_error(str(e)), str(e).splitlines()[0][:300]) from e

    async def metadata(self, url: str) -> VideoItem:
        info = await self._run(url, self._opts(skip_download=True), False, self.timeout_s)
        return info_to_video_item(info)

    async def search_youtube(self, query: str, n: int = 10, max_duration_s: float = 180.0) -> list[VideoItem]:
        info = await self._run(f"ytsearch{n}:{query}", self._opts(extract_flat="in_playlist", noplaylist=False),
                               False, self.timeout_s)
        items = []
        for e in info.get("entries") or []:
            vid, duration = e.get("id"), e.get("duration")
            if not vid or (duration is not None and duration > max_duration_s):
                continue
            items.append(VideoItem(
                canonical_id=f"youtube:{vid}", platform="youtube", url=f"https://www.youtube.com/shorts/{vid}",
                creator=Creator(handle=norm_handle(e["channel"]) if e.get("channel") else None),
                caption=e.get("title"), duration_s=float(duration) if duration is not None else None,
                metrics=Metrics(views=_int(e.get("view_count"))), media_access="ok", source="yt-dlp"))
        return items

    async def download(self, url: str, dest_dir: Path, max_height: int = 720) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        fmt = DOWNLOAD_FORMAT.replace("720", str(max_height))
        opts = self._opts(format=fmt, merge_output_format="mp4", outtmpl=str(dest_dir / "%(id)s.%(ext)s"))
        info = await self._run(url, opts, True, self.timeout_s * 4)
        files = [d.get("filepath") for d in info.get("requested_downloads") or [] if d.get("filepath")]
        if not files or not Path(files[0]).is_file():
            raise ToolFailure("platform_unavailable", "download produced no file")
        return Path(files[0])
