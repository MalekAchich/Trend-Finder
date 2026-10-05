"""analyze_video: download → probe → cuts → frames → contact sheet → pose/motion/transcript → feasibility → persist.

CPU-heavy steps (MediaPipe, optical flow, whisper) run in a process pool so the event loop stays responsive while
eight agents work in parallel (04-video-pipeline.md, D-15/D-16/D-26).
"""
from __future__ import annotations

import asyncio
import atexit
import multiprocessing
import shutil
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import ProcessPoolExecutor
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
from tf_agent.pipeline.retention import MediaRetention
from tf_agent.pipeline.sheet import make_contact_sheet
from tf_agent.pipeline.transcript import Transcriber
from tf_agent.tools.types import ToolFailure, VideoItem

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
        self._pool = ProcessPoolExecutor(max_workers=max_workers, mp_context=multiprocessing.get_context("spawn"),
                                         initializer=_worker_init, initargs=(whisper_model,))

    async def __call__(self, job: HeavyJob) -> HeavyResult:
        return await asyncio.get_running_loop().run_in_executor(self._pool, _worker_run, job)

    def close(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)


class Downloader(Protocol):
    async def download(self, url: str, dest_dir: Path, max_height: int = 720) -> Path: ...


class AnalysisStore(Protocol):
    async def get_analysis(self, canonical_id: str, version: str) -> VideoAnalysisResult | None: ...

    async def save_analysis(self, result: VideoAnalysisResult) -> None: ...

    async def upsert_videos(self, items: list[VideoItem]) -> None: ...


class VideoAnalyzer:
    def __init__(self, downloader: Downloader, *, media_dir: Path, heavy_runner: HeavyRunner,
                 store: AnalysisStore | None = None, retention: MediaRetention | None = None,
                 max_parallel: int = 2, fps: float = 2.0) -> None:
        self.downloader = downloader
        self.media_dir = Path(media_dir)
        self.runner = heavy_runner
        self.store = store
        self.retention = retention
        self.fps = fps
        self._slots = asyncio.Semaphore(max_parallel)
        self._in_progress: set[Path] = set()

    async def _save(self, result: VideoAnalysisResult) -> VideoAnalysisResult:
        if self.store is not None:
            await self.store.save_analysis(result)
        return result

    async def analyze(self, item: VideoItem) -> VideoAnalysisResult:
        if self.store is not None:
            existing = await self.store.get_analysis(item.canonical_id, PIPELINE_VERSION)
            if existing is not None:
                return existing
            await self.store.upsert_videos([item])
        if item.media_access == "login_required":
            return await self._save(VideoAnalysisResult.filtered(item.canonical_id, "media_unavailable"))
        async with self._slots:
            return await self._analyze_media(item)

    async def _analyze_media(self, item: VideoItem) -> VideoAnalysisResult:
        cid = item.canonical_id
        safe = cid.replace(":", "_")
        if self.retention is not None and not self.retention.can_download():
            self.retention.enforce(self._in_progress)
            if not self.retention.can_download():
                return VideoAnalysisResult.filtered(cid, "media_quota_full")  # not persisted: retry later
        try:
            video = await self.downloader.download(item.url, self.media_dir / "videos" / safe)
        except ToolFailure as e:
            result = VideoAnalysisResult.filtered(cid, f"download_failed:{e.error.code}")
            return await self._save(result) if e.error.code in PERMANENT_DOWNLOAD_FAILURES else result
        work = self.media_dir / "frames" / safe
        self._in_progress.add(video)
        try:
            try:
                meta = await probe(video)
            except PipelineError:
                return await self._save(VideoAnalysisResult.filtered(cid, "probe_failed", media_path=str(video)))
            cuts = await scene_cuts(video)
            frames = await sample_frames(video, work / "analysis", fps=self.fps)
            keys = await key_frames(video, work / "keys", meta.duration_s, cuts)
            sheet = make_contact_sheet(keys, self.media_dir / "sheets" / f"{safe}.jpg")
            heavy = await self.runner(HeavyJob(str(video), [str(p) for _, p in frames], meta.has_audio,
                                               meta.duration_s))
            stats = PoseStats(**heavy.pose)
            segments = clean_segments([t for t, _ in frames], stats.per_frame_single, cuts, heavy.motion_per_frame)
            best = segments[0] if segments else None
            cut_rate = len(cuts) / max(meta.duration_s, 1e-6) * 10
            score, reason = feasibility(stats, heavy.camera_motion, cut_rate, best)
            return await self._save(VideoAnalysisResult(
                canonical_id=cid, probe=meta.as_dict(), cuts=cuts, cut_rate=round(cut_rate, 3),
                transcript=heavy.transcript, pose=heavy.pose, camera_motion=round(heavy.camera_motion, 3),
                best_clean_segment={"start_s": best[0], "end_s": best[1]} if best else None,
                feasibility=score, filtered_reason=reason,
                fingerprint={"frame_hashes": frame_hashes([p for _, p in keys])},
                contact_sheet_path=str(sheet), media_path=str(video)))
        finally:
            shutil.rmtree(work, ignore_errors=True)
            self._in_progress.discard(video)
            if self.retention is not None:
                self.retention.enforce(self._in_progress)
