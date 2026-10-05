"""Group reposts of the same trend: same sound + similar frames, or near-identical frames (D-19)."""
from __future__ import annotations

from dataclasses import dataclass

from tf_agent.pipeline.fingerprint import hash_distance

SOUND_HASH_THRESHOLD = 12.0  # same sound and loosely similar frames → same trend
HASH_ONLY_THRESHOLD = 8.0  # visually near-identical without sound evidence → same video reposted


@dataclass
class Member:
    finding_id: str
    canonical_id: str
    sound_id: str | None
    frame_hashes: list[str]
    feasibility: float | None
    height: int | None
    momentum: float | None
    overall: float | None


def _same_trend(a: Member, b: Member) -> bool:
    if a.canonical_id == b.canonical_id:
        return True
    distance = hash_distance(a.frame_hashes, b.frame_hashes)
    if a.sound_id and a.sound_id == b.sound_id and distance < SOUND_HASH_THRESHOLD:
        return True
    return distance < HASH_ONLY_THRESHOLD


def cluster_members(members: list[Member]) -> list[list[Member]]:
    parent = list(range(len(members)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            if _same_trend(members[i], members[j]):
                parent[find(i)] = find(j)
    groups: dict[int, list[Member]] = {}
    for i, m in enumerate(members):
        groups.setdefault(find(i), []).append(m)
    return list(groups.values())


def best_source(cluster: list[Member]) -> Member:
    """The cleanest version to use as the Kling motion reference: feasibility, then resolution, then momentum."""
    return max(cluster, key=lambda m: (m.feasibility or 0.0, m.height or 0, m.momentum or 0.0))
