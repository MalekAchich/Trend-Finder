"""Everything the web API needs, built once per process (tests inject fakes)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.learning.briefs import BriefWriter
from tf_agent.learning.learner import Learner
from tf_backend.runs import RunManager


@dataclass
class AppContext:
    sessionmaker: async_sessionmaker[AsyncSession]
    runs: RunManager
    learner: Learner
    briefs: BriefWriter
    media_dir: Path
    characters_dir: Path
    orchestrator: Any = None
    registry: Any = None
    sse_poll_s: float = 0.05
    extras: dict[str, Any] = field(default_factory=dict)
