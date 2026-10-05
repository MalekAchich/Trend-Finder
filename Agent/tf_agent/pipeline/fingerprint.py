"""Perceptual frame hashes for clustering reposts of the same trend (D-19; audio fingerprint not available)."""
from __future__ import annotations

from pathlib import Path

import imagehash
from PIL import Image

MAX_DISTANCE = 64.0


def frame_hashes(frames: list[Path]) -> list[str]:
    out = []
    for p in frames:
        with Image.open(p) as im:
            out.append(str(imagehash.phash(im)))
    return out


def hash_distance(a: list[str], b: list[str]) -> float:
    """Mean Hamming distance between index-aligned hashes (0 = identical, 64 = unrelated)."""
    n = min(len(a), len(b))
    if n == 0:
        return MAX_DISTANCE
    return sum(imagehash.hex_to_hash(x) - imagehash.hex_to_hash(y) for x, y in zip(a[:n], b[:n], strict=True)) / n
