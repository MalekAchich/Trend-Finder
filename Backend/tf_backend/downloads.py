"""The owner's downloads of a video (with sound) or its sound alone. Each file is fetched once and kept for a few
days, so a second click is instant; only videos the app already knows are ever downloaded."""
from __future__ import annotations

import asyncio
import shutil
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

Save = Callable[[str, Path, str], Awaitable[Path]]  # (page url, folder, "video" | "audio") -> the saved file
KEEP_S = 3 * 24 * 3600


class Downloads:
    def __init__(self, save: Save, root: Path, clock: Callable[[], float] = time.time) -> None:
        self._save, self.root, self._clock = save, root, clock
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def _folder(self, cid: str, kind: str) -> Path:
        return self.root / kind / cid.replace(":", "_")

    @staticmethod
    def _ready(folder: Path) -> Path | None:
        files = [f for f in folder.iterdir() if f.is_file()] if folder.is_dir() else []
        return files[0] if files else None

    def _prune(self) -> None:
        for folder in self.root.glob("*/*"):
            if folder.is_dir() and self._clock() - folder.stat().st_mtime > KEEP_S:
                shutil.rmtree(folder, ignore_errors=True)

    async def get(self, cid: str, url: str, kind: str) -> Path:
        folder = self._folder(cid, kind)
        async with self._locks.setdefault((cid, kind), asyncio.Lock()):  # two clicks: one download
            if (done := self._ready(folder)) is not None:
                return done
            self._prune()
            staging = folder.with_name(folder.name + ".partial")
            shutil.rmtree(staging, ignore_errors=True)
            try:
                saved = await self._save(url, staging, kind)
                folder.parent.mkdir(parents=True, exist_ok=True)
                staging.rename(folder)  # only complete downloads are ever served
            finally:
                shutil.rmtree(staging, ignore_errors=True)
            return folder / saved.name
