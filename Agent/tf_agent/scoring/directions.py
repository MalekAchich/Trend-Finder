"""Direction keys: normalized slugs; near-duplicates proposed by the Master are merged into existing ones."""
from __future__ import annotations

import difflib
import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_key(key: str) -> str:
    return _NON_ALNUM.sub("-", key.lower()).strip("-")[:128]


def match_direction_key(key: str, existing: list[str], threshold: float = 0.85) -> str | None:
    nk = normalize_key(key)
    if nk in existing:
        return nk
    best, best_ratio = None, 0.0
    for e in existing:
        ratio = difflib.SequenceMatcher(None, nk, e).ratio()
        if ratio > best_ratio:
            best, best_ratio = e, ratio
    return best if best_ratio >= threshold else None
