"""Common matcher interface.

A matcher receives the STANDARDIZED records (never the answer key) and returns scored candidate pairs with a
three-way decision: "merge" (auto-merge), "review" (human review band) or "non-match". Only "merge" pairs form
clusters; the review band is surfaced separately (Phase 4).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd

MERGE, REVIEW, NON_MATCH = "merge", "review", "non-match"
PAIR_COLUMNS = ["a_id", "b_id", "score", "decision", "evidence", "guard_ok"]


@dataclass
class MatchOutput:
    pairs: pd.DataFrame  # PAIR_COLUMNS; every candidate pair that was scored (a_id < b_id)
    n_candidates: int
    runtime_s: float
    info: dict = field(default_factory=dict)

    def merged(self) -> list[tuple[str, str]]:
        sub = self.pairs[self.pairs.decision == MERGE]
        return list(zip(sub.a_id, sub.b_id))

    def merged_at(self, threshold: float) -> list[tuple[str, str]]:
        """Pairs that would merge if the merge threshold were `threshold` (guards still applied). PR-curve helper."""
        sub = self.pairs[(self.pairs.score >= threshold) & self.pairs.guard_ok]
        return list(zip(sub.a_id, sub.b_id))


class Matcher(Protocol):
    name: str

    def match(self, clean: pd.DataFrame) -> MatchOutput: ...


def timed(fn):
    """Decorator: fill MatchOutput.runtime_s with wall-clock seconds spent in the wrapped match()."""

    def wrapper(self, clean):
        t0 = time.perf_counter()
        out = fn(self, clean)
        out.runtime_s = time.perf_counter() - t0
        return out

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def empty_pairs() -> pd.DataFrame:
    return pd.DataFrame(columns=PAIR_COLUMNS)
