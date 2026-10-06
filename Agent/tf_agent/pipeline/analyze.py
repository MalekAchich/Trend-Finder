"""analyze_video: download → probe → cuts → frames → contact sheet → pose/motion/transcript → feasibility → persist.

CPU-heavy steps (MediaPipe, optical flow, whisper) run in a process pool so the event loop stays responsive while
eight agents work in parallel (04-video-pipeline.md, D-15/D-16/D-26).
"""
from __future__ import annotations

import asyncio
import atexit
import logging
import multiprocessing
import shutil
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from tf_agent.pipeline import PIPELINE_VERSION
from tf_agent.pipeline.feasibility import clean_segments, feasibility
from tf_agent.pipeline.ffmpeg import PipelineError, probe, scene_cuts
from tf_agent.pipeline.fingerprint import frame_hashes
from tf_agent.pipeline.frames import key_frames, sample_frames
from tf_agent.pipeline.motion import camera_motion
from tf_agent.pipeline.pose import PoseAnalyzer, PoseStats
from tf_agent.pipeline.result import VideoAnalysisResult
from tf_agent.pipeline.media import make_thumbnail
from tf_agent.pipeline.sheet import make_contact_sheet
from tf_agent.pipeline.transcript import Transcriber
from tf_agent.tools.normalize import canonical_id, platform_of
from tf_agent.tools.types import ToolFailure, VideoItem

log = logging.getLogger(__name__)

PERMANENT_DOWNLOAD_FAILURES = ("not_found", "login_required")


@dataclass
class HeavyJob:
    video_path: str
    frame_paths: list[str]
    has_audio: bool
    duration_s: float


@dataclass
class HeavyResult:
    pose: dict[str, Any]
    camera_motion: float
    motion_per_frame: list[float]
    transcript: dict[str, Any]


HeavyRunner = Callable[[HeavyJob], Awaitable[HeavyResult]]


def run_heavy(job: HeavyJob, pose: PoseAnalyzer, transcriber: Transcriber) -> HeavyResult:
    frames = [Path(p) for p in job.frame_paths]
    stats = pose.analyze(frames)
    motion, per_frame = camera_motion(frames)
    transcript = transcriber.transcribe(Path(job.video_path), job.has_audio, job.duration_s)
    return HeavyResult(stats.as_dict(), motion, per_frame, transcript.as_dict())


class thread_runner:  # noqa: N801 (used like a function factory)
    """Runs heavy steps in a worker thread (tests, low-volume CLI use). MediaPipe isn't thread-safe: serialized."""

    def __init__(self, pose: PoseAnalyzer, transcriber: Transcriber) -> None:
        self._pose, self._transcriber = pose, transcriber
        self._lock = threading.Lock()

    def _run(self, job: HeavyJob) -> HeavyResult:
        with self._lock:
            return run_heavy(job, self._pose, self._transcriber)

    async def __call__(self, job: HeavyJob) -> HeavyResult:
        return await asyncio.to_thread(self._run, job)

    def close(self) -> None:
        with self._lock:
            self._pose.close()


_WORKER: tuple[PoseAnalyzer, Transcriber] | None = None


def _worker_init(whisper_model: str) -> None:
    global _WORKER
    _WORKER = (PoseAnalyzer(), Transcriber(model_size=whisper_model))
    atexit.register(_WORKER[0].close)  # close MediaPipe before interpreter teardown (see PoseAnalyzer.close)


def _worker_run(job: HeavyJob) -> HeavyResult:
    assert _WORKER is not None
    return run_heavy(job, *_WORKER)


class process_pool_runner:  # noqa: N801
    """Heavy steps in `max_workers` spawned processes; models load once per worker."""

    def __init__(self, max_workers: int = 2, whisper_model: str = "small") -> None:
        self._args = (max_workers, whisper_model)
        self._pool = self._new_pool()

    def _new_pool(self) -> ProcessPoolExecutor:
        max_workers, whisper_model = self._args
        return ProcessPoolExecutor(max_workers=max_workers, mp_context=multiprocessing.get_context("spawn"),
                                   initializer=_worker_init, initargs=(whisper_model,))

    async def __call__(self, job: HeavyJob) -> HeavyResult:
        try:
            return await asyncio.get_running_loop().run_in_executor(self._pool, _worker_run, job)
        except BrokenProcessPool:  # a worker died (OOM, segfault): recreate the pool for the next job
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = self._new_pool()
            raise

    def close(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)


class Downloader(Protocol):
    async def download(self, url: str, dest_dir: Path, max_height: int = 720) -> Path: ...


class AnalysisStore(Protocol):
    async def get_analysis(self, canonical_id: str, version: str) -> VideoAnalysisResult | None: ...

    async def save_analysis(self, result: VideoAnalysisResult) -> None: ...

    async def upsert_videos(self, items: list[VideoItem]) -> None: ...


class VideoAnalyzer:
    """One shared instance per process: analyses are single-flight per video and bounded by `max_parallel`."""

    def __init__(self, downloader: Downloader, *, media_dir: Path, heavy_runner: HeavyRunner,
                 store: AnalysisStore | None = None, max_parallel: int = 2, fps: float = 2.0, max_duration_s: float = 600.0,
                 heavy_timeout_s: float = 300.0) -> None:
        self.downloader = downloader
        self.media_dir = Path(media_dir)
        self.runner = heavy_runner
        self.store = store
        self.fps = fps
        self.max_duration_s = max_duration_s
        self.heavy_timeout_s = heavy_timeout_s
        self._slots = asyncio.Semaphore(max_parallel)
        self._inflight: dict[str, asyncio.Future[VideoAnalysisResult]] = {}

    async def _save(self, result: VideoAnalysisResult) -> VideoAnalysisResult:
        if self.store is not None:
            await self.store.save_analysis(result)
        return result

    async def analyze(self, item: VideoItem) -> VideoAnalysisResult:
        cid = item.canonical_id
        if cid in self._inflight:  # another agent already asked for this video: share the work
            return await asyncio.shield(self._inflight[cid])
        future: asyncio.Future[VideoAnalysisResult] = asyncio.get_running_loop().create_future()
        self._inflight[cid] = future
        try:
            result = await self._analyze(item)
            future.set_result(result)
            return result
        except BaseException as e:
            if not future.done():
                future.set_exception(e)
                future.exception()  # mark retrieved: waiters re-raise it, nobody else must
            raise
        finally:
            self._inflight.pop(cid, None)

    async def _analyze(self, item: VideoItem) -> VideoAnalysisResult:
        cid = item.canonical_id
        if canonical_id(item.url) not in (cid, None) or platform_of(item.url) != item.platform:
            return VideoAnalysisResult.filtered(cid, "invalid_item")  # never fetch a URL that isn't this video
        if self.store is not None:
            existing = await self.store.get_analysis(cid, PIPELINE_VERSION)
            stale = (existing is not None and not existing.filtered_reason
                     and not (existing.contact_sheet_path and Path(existing.contact_sheet_path).exists()))
            if existing is not None and not stale:  # stale: the sheet was cleaned up after an earlier run
                return existing
            await self.store.upsert_videos([item])
        if item.media_access == "login_required":
            return await self._save(VideoAnalysisResult.filtered(cid, "media_unavailable"))
        if item.duration_s is not None and item.duration_s > self.max_duration_s:
            return await self._save(VideoAnalysisResult.filtered(cid, "too_long"))
        async with self._slots:
            return await self._analyze_media(item)

    async def _analyze_media(self, item: VideoItem) -> VideoAnalysisResult:
        cid = item.canonical_id
        safe = cid.replace(":", "_")  # canonical IDs are validated: [A-Za-z0-9_-] only
        video_dir, work = self.media_dir / "videos" / safe, self.media_dir / "frames" / safe
        try:
            try:
                video = await self.downloader.download(item.url, video_dir)
            except ToolFailure as e:
                result = VideoAnalysisResult.filtered(cid, f"download_failed:{e.error.code}")
                return await self._save(result) if e.error.code in PERMANENT_DOWNLOAD_FAILURES else result
            try:
                meta = await probe(video)
            except (PipelineError, TimeoutError, ValueError, KeyError):
                return await self._save(VideoAnalysisResult.filtered(cid, "probe_failed"))
            if meta.duration_s > self.max_duration_s:
                return await self._save(VideoAnalysisResult.filtered(cid, "too_long"))
            step, sheet = "frames", None
            try:
                cuts = await scene_cuts(video)
                frames = await sample_frames(video, work / "analysis", fps=self.fps)
                keys = await key_frames(video, work / "keys", meta.video_duration_s or meta.duration_s, cuts)
                sheet = make_contact_sheet(keys, self.media_dir / "sheets" / f"{safe}.jpg")
                step = "heavy"
                job = HeavyJob(str(video), [str(p) for _, p in frames], meta.has_audio, meta.duration_s)
                try:
                    heavy = await asyncio.wait_for(self.runner(job), self.heavy_timeout_s)
                except TimeoutError:
                    return VideoAnalysisResult.filtered(cid, "analysis_failed:heavy_timeout",
                                                        contact_sheet_path=str(sheet))
            except (PipelineError, TimeoutError, ValueError, OSError, BrokenProcessPool, RuntimeError,
                    AttributeError, ExceptionGroup) as e:
                log.warning("analysis of %s failed at %s: %s", cid, step, e)
                return VideoAnalysisResult.filtered(cid, f"analysis_failed:{step}",
                                                    contact_sheet_path=str(sheet) if sheet else None)
            stats = PoseStats(**heavy.pose)
            segments = clean_segments([t for t, _ in frames], stats.per_frame_single, cuts, heavy.motion_per_frame)
            best = segments[0] if segments else None
            cut_rate = len(cuts) / max(meta.duration_s, 1e-6) * 10
            score, reason = feasibility(stats, heavy.camera_motion, cut_rate, best)
            at = (best[0] + best[1]) / 2 if best else meta.duration_s / 2
            thumb = await self._thumbnail(video, self.media_dir / "thumbs" / f"{safe}.jpg", at)
            return await self._save(VideoAnalysisResult(
                canonical_id=cid, probe=meta.as_dict(), cuts=cuts, cut_rate=round(cut_rate, 3),
                transcript=heavy.transcript, pose=heavy.pose, camera_motion=round(heavy.camera_motion, 3),
                best_clean_segment={"start_s": best[0], "end_s": best[1]} if best else None,
                feasibility=score, filtered_reason=reason,
                fingerprint={"frame_hashes": frame_hashes([p for _, p in keys])},
                contact_sheet_path=str(sheet), thumbnail_path=thumb))
        finally:  # the video itself is never kept (only its URL, numbers and thumbnail)
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(video_dir, ignore_errors=True)

    async def _thumbnail(self, video: Path, out: Path, at_s: float) -> str | None:
        try:
            return str(await make_thumbnail(video, out, at_s))
        except (PipelineError, TimeoutError, OSError) as e:  # a missing thumbnail never costs the analysis
            log.warning("thumbnail for %s failed: %s", video, e)
            return None
