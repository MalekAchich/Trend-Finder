"""profile.md = YAML front matter + `## Section` markdown (04-character-profile-format.md)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

_FRONT_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)


class CharacterError(Exception):
    pass


@dataclass
class Profile:
    front: dict[str, Any]
    sections: dict[str, str] = field(default_factory=dict)
    headings: dict[str, str] = field(default_factory=dict)
    niche_open: bool = False

    @property
    def name(self) -> str:
        return str(self.front.get("name") or self.front.get("slug") or "unnamed")

    @property
    def slug(self) -> str:
        return str(self.front.get("slug") or self.name).strip().lower()

    @property
    def canonical_image(self) -> str | None:
        return self.front.get("canonical_image")

    def section(self, prefix: str) -> str:
        return next((body for key, body in self.sections.items() if key.startswith(prefix)), "")


def parse_profile(text: str) -> Profile:
    m = _FRONT_RE.match(text)
    if not m:
        raise CharacterError("profile.md must start with YAML front matter between --- lines")
    try:
        front = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise CharacterError(f"invalid YAML front matter: {e}") from e
    sections: dict[str, str] = {}
    headings: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in text[m.end():].splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            heading = line[3:].strip()
            current = heading.split(":")[0].strip().lower()
            headings[current] = heading
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    niche_key = next((k for k in sections if k.startswith("niche")), None)
    niche_open = niche_key is not None and ("OPEN" in headings[niche_key] or sections[niche_key].startswith("OPEN"))
    return Profile(front=front, sections=sections, headings=headings, niche_open=niche_open)
