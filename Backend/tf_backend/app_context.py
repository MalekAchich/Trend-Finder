"""Everything the web API needs, built once per process (tests inject fakes)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.learning.learner import Learner
from tf_backend.runs import RunManager


@dataclass
class AppContext:
    sessionmaker: async_sessionmaker[AsyncSession]
    runs: RunManager
    learner: Learner
    media_dir: Path
    characters_dir: Path
    orchestrator: Any = None
    sse_poll_s: float = 0.05
    browser: Any = None  # BrowserSessions: scraping-account logins + their credential store
    youtube_api: Any = None  # YouTubeApi: checks a key before it's saved
    manual: Any = None  # ManualVideos: the owner's manually chosen videos
    previews: Any = None  # Previews: hover playback for Instagram and X
    downloads: Any = None  # Downloads: the owner's copies of a video or its sound
    socials: Any = None  # Socials: our own channels (sync loop, numbers, the TikTok login hand-off)
    extras: dict[str, Any] = field(default_factory=dict)
