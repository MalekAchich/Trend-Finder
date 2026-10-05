"""Versioned role prompts stored as markdown files (Agent/tf_agent/prompts/<role>.md)."""
from __future__ import annotations

import re
from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_VERSION_RE = re.compile(r"^<!--\s*version:\s*([\w.-]+)\s*-->\s*\n?")


class PromptLibrary:
    def __init__(self, directory: Path = PROMPTS_DIR) -> None:
        self.directory = directory
        self._cache: dict[str, str] = {}

    def raw(self, role: str) -> str:
        if role not in self._cache:
            self._cache[role] = (self.directory / f"{role}.md").read_text()
        return self._cache[role]

    def version(self, role: str) -> str:
        m = _VERSION_RE.match(self.raw(role))
        return m.group(1) if m else "0"

    def render(self, role: str, **values: str) -> str:
        body = _VERSION_RE.sub("", self.raw(role), count=1)
        return Template(body).substitute(values).strip()
