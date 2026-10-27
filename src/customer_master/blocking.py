"""Candidate generation ("blocking"): list only record pairs that share at least one cheap key.

Every rule maps a record to zero or more block keys; records sharing a key become candidate pairs. NULL keys never
form blocks. A block larger than rule.max_block is skipped and counted (a coarse key such as a very common name in one
city would otherwise create O(n^2) pairs); skipped blocks are reported so the recall cost is visible, not hidden.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations


@dataclass
class BlockingStats:
    n_records: int
    n_pairs: int = 0
    per_rule_pairs: dict = field(default_factory=dict)
    per_rule_skipped_blocks: dict = field(default_factory=dict)
    per_rule_skipped_records: dict = field(default_factory=dict)

    @property
    def reduction_ratio(self) -> float:
        total = self.n_records * (self.n_records - 1) / 2
        return 1 - self.n_pairs / total if total else 1.0


def generate_candidates(feats: list, rules: list) -> tuple[dict, BlockingStats]:
    """Return ({(i, j): bitmask of rules that produced it}, stats) with i < j indexing `feats`."""
    stats = BlockingStats(len(feats))
    cand: dict[tuple[int, int], int] = defaultdict(int)
    for r_idx, rule in enumerate(rules):
        blocks: dict = defaultdict(list)
        for i, f in enumerate(feats):
            for key in set(rule.keys(f)):
                blocks[key].append(i)
        produced = skipped_blocks = skipped_records = 0
        for members in blocks.values():
            if len(members) < 2:
                continue
            if len(members) > rule.max_block:
                skipped_blocks += 1
                skipped_records += len(members)
                continue
            for i, j in combinations(members, 2):
                if not cand[(i, j)] & (1 << r_idx):
                    produced += 1
                cand[(i, j)] |= 1 << r_idx
        stats.per_rule_pairs[rule.name] = produced
        stats.per_rule_skipped_blocks[rule.name] = skipped_blocks
        stats.per_rule_skipped_records[rule.name] = skipped_records
    stats.n_pairs = len(cand)
    return dict(cand), stats
