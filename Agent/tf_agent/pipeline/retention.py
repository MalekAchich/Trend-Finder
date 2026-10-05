"""Media quota (D-26): evict least-recently-used videos/frames, never protected files or contact sheets."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

EVICTABLE_DIRS = ("videos", "frames")
DOWNLOAD_STOP_FRACTION = 0.95


class MediaRetention:
    def __init__(self, media_dir: Path, quota_bytes: int, protected: Callable[[], Iterable[Path]]) -> None:
        self.media_dir = Path(media_dir)
        self.quota_bytes = quota_bytes
        self._protected = protected

    def _files(self, under: Iterable[str] | None = None) -> list[Path]:
        roots = [self.media_dir / d for d in under] if under else [self.media_dir]
        return [p for root in roots if root.exists() for p in root.rglob("*") if p.is_file()]

    def usage(self) -> int:
        return sum(p.stat().st_size for p in self._files())

    def can_download(self) -> bool:
        return self.usage() < self.quota_bytes * DOWNLOAD_STOP_FRACTION

    def enforce(self, extra_protected: Iterable[Path] = ()) -> int:
        """Delete oldest unprotected evictable files until usage ≤ quota. Returns bytes freed."""
        usage = self.usage()
        if usage <= self.quota_bytes:
            return 0
        protected = {Path(p).resolve() for p in [*self._protected(), *extra_protected]}
        candidates = sorted((p for p in self._files(EVICTABLE_DIRS) if p.resolve() not in protected),
                            key=lambda p: p.stat().st_mtime)
        freed = 0
        for p in candidates:
            if usage - freed <= self.quota_bytes:
                break
            size = p.stat().st_size
            p.unlink(missing_ok=True)
            freed += size
        for d in EVICTABLE_DIRS:  # drop empty frame folders left behind
            root = self.media_dir / d
            if root.exists():
                for sub in sorted((x for x in root.rglob("*") if x.is_dir()), reverse=True):
                    if not any(sub.iterdir()):
                        sub.rmdir()
        return freed
