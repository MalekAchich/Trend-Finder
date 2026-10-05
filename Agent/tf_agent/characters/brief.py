"""Compact character brief (~600 tokens) given to every agent (04-character-profile-format.md: rules)."""
from __future__ import annotations

from tf_agent.characters.profile import Profile

ORDER = [("persona", "Persona"), ("energy", "Energy & charisma"), ("comedic", "Comedic engine"),
         ("niche", "Niche / context"), ("look", "Look (fixed)"), ("kling", "Kling constraints"),
         ("do", "Do / Don't")]


def _clean(text: str) -> str:
    lines = [ln.lstrip("> ").rstrip() for ln in text.splitlines() if ln.strip() and not ln.startswith("> DRAFT")]
    return "\n".join(lines)


def build_brief(profile: Profile, max_chars: int = 2400) -> str:
    parts = [f"# Character: {profile.name}"]
    for prefix, title in ORDER:
        body = _clean(profile.section(prefix))
        if prefix == "niche" and profile.niche_open:
            title = "Niche / context: OPEN (not decided: the system must discover which niche works)"
        if body:
            parts.append(f"## {title}\n{body}")
    brief = "\n\n".join(parts)
    if len(brief) <= max_chars:
        return brief
    # shrink the longest sections first, never the header
    budget = (max_chars - len(parts[0]) - 2 * len(parts)) // max(len(parts) - 1, 1)
    trimmed = [parts[0]] + [p if len(p) <= budget else p[: budget - 1].rstrip() + "…" for p in parts[1:]]
    return "\n\n".join(trimmed)[:max_chars]
