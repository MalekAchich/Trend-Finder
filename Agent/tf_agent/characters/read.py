"""The reader's take on a character's images, and the compact brief every other role receives."""
from __future__ import annotations

from pydantic import BaseModel, Field


class CharacterRead(BaseModel):
    look: str = Field(min_length=3, max_length=600, description="what is fixed and visible: face, hair, outfit, era")
    vibe: str = Field(min_length=3, max_length=400, description="attitude and energy the images project")
    performance_angle: str = Field(min_length=3, max_length=500,
                                   description="how this character would be funny or compelling on camera")
    possible_niches: list[str] = Field(min_length=3, max_length=6)
    kling_constraints: str = Field(min_length=3, max_length=400,
                                   description="what Kling Motion Control can and can't do with this look")
    avoid: str = Field("", max_length=400)


def render_brief(name: str, read: CharacterRead, taste_md: str | None, max_chars: int = 2400) -> str:
    parts = [
        f"# Character: {name}",
        f"## Look (fixed)\n{read.look}",
        f"## Vibe\n{read.vibe}",
        f"## Performance angle\n{read.performance_angle}",
        "## Possible niches (not decided: find which works)\n" + "\n".join(f"- {n}" for n in read.possible_niches),
        f"## Kling constraints\n{read.kling_constraints}",
    ]
    if read.avoid:
        parts.append(f"## Avoid\n{read.avoid}")
    head = "\n\n".join(parts)
    if taste_md:
        room = max_chars - len(head) - len("\n\n## What the owner liked so far\n")
        if room > 80:
            taste = taste_md.strip()
            head += "\n\n## What the owner liked so far\n" + (taste if len(taste) <= room else taste[: room - 1] + "…")
    return head[:max_chars]
