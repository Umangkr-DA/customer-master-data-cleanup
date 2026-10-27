"""Union-find clustering: connected components of accepted match pairs."""

from __future__ import annotations

from typing import Iterable


class UnionFind:
    def __init__(self, items: Iterable[str] = ()):
        self.parent: dict[str, str] = {x: x for x in items}

    def find(self, x: str) -> str:
        parent = self.parent
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:  # path compression
            parent[x], x = root, parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # smaller id becomes the root so labels are deterministic and match the SQL min-label propagation
            if rb < ra:
                ra, rb = rb, ra
            self.parent[rb] = ra


def cluster(record_ids: Iterable[str], merged_pairs: Iterable[tuple[str, str]]) -> dict[str, str]:
    """record_id -> cluster label (the smallest record_id in the component)."""
    uf = UnionFind(record_ids)
    for a, b in merged_pairs:
        uf.union(a, b)
    return {r: uf.find(r) for r in uf.parent}
