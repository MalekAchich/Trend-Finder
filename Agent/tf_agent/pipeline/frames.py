"""Frame sampling for analysis (2 fps) and key frames for the contact sheet."""
from __future__ import annotations

import asyncio
from pathlib import Path

from tf_agent.pipeline.ffmpeg import PipelineError, run

Frame = tuple[float, Path]


async def sample_frames(path: Path, out_dir: Path, fps: float = 2.0, width: int = 360) -> list[Frame]:
    out_dir.mkdir(parents=True, exist_ok=True)
    await run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(path), "-vf", f"fps={fps},scale={width}:-2",
               "-q:v", "4", str(out_dir / "f_%05d.jpg")])
    files = sorted(out_dir.glob("f_*.jpg"))
    if not files:  # clips shorter than one sampling interval: take the first frame
        await run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(path), "-frames:v", "1", "-vf", f"scale={width}:-2",
                   str(out_dir / "f_00001.jpg")])
        files = sorted(out_dir.glob("f_*.jpg"))
    if not files:
        raise PipelineError("no frames could be sampled")
    return [(round(i / fps, 3), f) for i, f in enumerate(files)]


def _key_times(duration_s: float, cuts: list[float], n: int, margin: float = 0.15) -> list[float]:
    times = []
    for i in range(n):
        t = (i + 0.5) * duration_s / n
        for c in cuts:  # never sample the transition frame itself: step back/forward to the frame's own side
            if abs(t - c) < margin:
                after = c + margin + 0.02
                t = c - margin - 0.02 if (t < c or after >= duration_s) else after
        times.append(round(min(max(t, 0.0), max(duration_s - 0.05, 0.0)), 3))
    return sorted(times)


async def key_frames(path: Path, out_dir: Path, duration_s: float, cuts: list[float], n: int = 12,
                     width: int = 480) -> list[Frame]:
    out_dir.mkdir(parents=True, exist_ok=True)
    slots = asyncio.Semaphore(4)

    async def grab(i: int, t: float) -> Frame:
        dest = out_dir / f"k_{i:02d}.jpg"
        async with slots:
            await run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                       "-vf", f"scale={width}:-2", "-q:v", "3", str(dest)])
        if not dest.exists():
            raise PipelineError(f"key frame at {t:.2f}s missing")
        return t, dest

    async with asyncio.TaskGroup() as tg:  # a failed grab cancels its siblings (gather would leave them running)
        tasks = [tg.create_task(grab(i, t)) for i, t in enumerate(_key_times(duration_s, cuts, n))]
    return [t.result() for t in tasks]
