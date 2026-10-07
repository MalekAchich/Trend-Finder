"""yt-dlp adapter: metadata, ≤720p downloads and YouTube search for any platform URL (03-tools.md: get_video)."""
from __future__ import annotations

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tf_agent.tools.normalize import canonical_id, norm_handle, norm_hashtag, platform_of
from tf_agent.tools.types import Creator, Metrics, Sound, ToolErrorCode, ToolFailure, VideoItem

Extractor = Callable[[str, dict[str, Any], bool], dict[str, Any]]
CookiesFor = Callable[[str], Path | None]  # platform -> the scraping account's cookies.txt, read at call time
_HASHTAG_RE = re.compile(r"#(\w+)", re.UNICODE)
# Only the platforms we support: never the generic extractor (which would fetch arbitrary URLs).
ALLOWED_EXTRACTORS = ["tiktok.*", "vm\\.tiktok", "instagram.*", "youtube.*", "twitter.*"]
PREVIEW_FORMAT = "b[height<=720][ext=mp4]/bv*[height<=720][ext=mp4]/b[height<=720]/b"
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
    if cid is None and "twitter" in extractor and info.get("id"):
        cid = f"x:{str(info['id']).split('_')[0]}"  # multi-video posts get "<status id>_<n>" ids
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
    if platform == "youtube":
        handle = info.get("uploader_id")
    elif platform == "instagram":  # `uploader` is sometimes the display name; `channel` is the username
        handle = info.get("channel") or info.get("uploader")
    else:
        handle = info.get("uploader") or info.get("uploader_id")
    ts = info.get("timestamp")
    track = (info.get("track") or "").strip() or None
    artists = info.get("artists") or ([info["artist"]] if info.get("artist") else [])
    sound_key = None
    if track and track.lower() != "original sound":  # a bare "original sound" says nothing about the audio
        sound_key = f"track:{track.lower()}|{(artists[0] if artists else '').lower()}"[:128]
    return VideoItem(
        canonical_id=cid, platform=platform, url=url,
        creator=Creator(handle=norm_handle(str(handle)) if handle else None,
                        followers=_int(info.get("channel_follower_count"))),
        caption=caption, hashtags=tags,
        sound=Sound(id=sound_key, title=track),
        posted_at=datetime.fromtimestamp(ts, UTC) if ts else None,
        duration_s=float(info["duration"]) if info.get("duration") is not None else None,
        metrics=Metrics(views=_int(info.get("view_count")), likes=_int(info.get("like_count")),
                        comments=_int(info.get("comment_count")), shares=_int(info.get("repost_count")),
                        saves=_int(info.get("save_count"))),
        thumbnail_url=info.get("thumbnail"), media_access="ok", source="yt-dlp",
    )


class YtDlp:
    def __init__(self, timeout_s: float = 45.0, max_parallel: int = 2, cookies_file: Path | None = None,
                 extractor: Extractor | None = None, cookies_for: CookiesFor | None = None) -> None:
        self.timeout_s = timeout_s
        self.cookies_file = cookies_file
        self.cookies_for = cookies_for
        self._extract = extractor or _default_extract
        # a dedicated pool: timed-out calls keep their thread until they finish, so the bound really holds
        self._pool = ThreadPoolExecutor(max_workers=max_parallel, thread_name_prefix="yt-dlp")

    def _cookies(self, url: str | None) -> Path | None:
        platform = platform_of(url) if url else None
        if self.cookies_for is not None and platform is not None and (f := self.cookies_for(platform)) is not None:
            return f
        return self.cookies_file

    def _opts(self, url: str | None = None, **extra: Any) -> dict[str, Any]:
        opts: dict[str, Any] = {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
                                "socket_timeout": 20,
                                "allowed_extractors": ALLOWED_EXTRACTORS}
        if (cookies := self._cookies(url)) is not None:
            opts["cookiefile"] = str(cookies)
        opts.update(extra)
        return opts

    async def _run(self, url: str, opts: dict[str, Any], download: bool, timeout_s: float) -> dict[str, Any]:
        future = asyncio.get_running_loop().run_in_executor(self._pool, self._extract, url, opts, download)
        try:
            return await asyncio.wait_for(future, timeout_s)  # on timeout a queued job is cancelled
        except TimeoutError:
            raise ToolFailure("platform_unavailable", f"yt-dlp timed out after {timeout_s}s") from None
        except ToolFailure:
            raise
        except Exception as e:  # yt_dlp.utils.DownloadError and friends
            raise ToolFailure(classify_ytdlp_error(str(e)), str(e).splitlines()[0][:300]) from e

    async def metadata(self, url: str) -> VideoItem:
        info = await self._run(url, self._opts(url, skip_download=True), False, self.timeout_s)
        return info_to_video_item(info)

    async def stream_url(self, url: str) -> str:
        """A direct, short-lived link to the video itself (≤ 720p), for a hover preview: nothing is downloaded."""
        info = await self._run(url, self._opts(url, skip_download=True, format=PREVIEW_FORMAT), False, self.timeout_s)
        direct = info.get("url") or next((f.get("url") for f in info.get("requested_formats") or [] if f.get("url")), None)
        if not direct:
            raise ToolFailure("platform_unavailable", "no playable stream for this video")
        return str(direct)

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
        opts = self._opts(url, format=fmt, merge_output_format="mp4", outtmpl=str(dest_dir / "%(id)s.%(ext)s"))
        info = await self._run(url, opts, True, self.timeout_s * 4)
        files = [d.get("filepath") for d in info.get("requested_downloads") or [] if d.get("filepath")]
        if not files or not Path(files[0]).is_file():
            raise ToolFailure("platform_unavailable", "download produced no file")
        return Path(files[0])
