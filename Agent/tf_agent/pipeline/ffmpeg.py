"""ffmpeg/ffprobe helpers: probe, scene cuts, and a cancellation-safe subprocess runner."""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


class PipelineError(Exception):
    pass


@dataclass(frozen=True)
class Probe:
    duration_s: float
    width: int
    height: int
    fps: float
    has_audio: bool
    video_duration_s: float = 0.0  # the video stream's own length (can be shorter than the container)

    def as_dict(self) -> dict:
        return asdict(self)


async def run(cmd: list[str], timeout_s: float = 180.0) -> tuple[str, str]:
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
        await asyncio.shield(proc.wait())
        raise
    stdout, stderr = out.decode(errors="replace"), err.decode(errors="replace")
    if proc.returncode != 0:
        raise PipelineError(f"{Path(cmd[0]).name} failed ({proc.returncode}): {stderr.strip()[-300:]}")
    return stdout, stderr


def _fps(rate: str | None) -> float:
    if not rate or rate == "0/0":
        return 0.0
    num, _, den = rate.partition("/")
    return float(num) / float(den or 1)


async def probe(path: Path) -> Probe:
    out, _ = await run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_type,width,height,r_frame_rate,duration:format=duration", "-of", "json",
                        str(path)], 60)
    data = json.loads(out or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    duration = float((data.get("format") or {}).get("duration") or 0.0)
    if video is None or duration <= 0:
        raise PipelineError("no decodable video stream")
    video_duration = float(video.get("duration") or duration)
    return Probe(duration_s=duration, width=int(video["width"]), height=int(video["height"]),
                 fps=_fps(video.get("r_frame_rate")), has_audio=any(s.get("codec_type") == "audio" for s in streams),
                 video_duration_s=min(video_duration, duration))


_PTS_RE = re.compile(r"pts_time:([0-9.]+)")


async def scene_cuts(path: Path, threshold: float = 0.3) -> list[float]:
    _, err = await run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vf",
                        f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"])
    return [round(float(m), 3) for m in _PTS_RE.findall(err)]
