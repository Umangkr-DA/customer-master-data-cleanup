"""Reviewer SIMULATION. This uses the answer key to play the role of a human reviewer, so every number it produces is a
simulation of a reviewer with stated accuracy, not a measurement of real review. Evaluation code only."""

from __future__ import annotations

import random

import pandas as pd

from customer_master.evaluation import evaluate_clusters
from customer_master.matchers.base import MERGE, MatchOutput


def simulate_review(
    ids: list,
    entity_of: dict,
    out: MatchOutput,
    queue: pd.DataFrame,
    order: str = "ev",
    accuracy: float = 1.0,
    seed: int = 0,
    step: int = 50,
) -> pd.DataFrame:
    """Metrics after the reviewer has judged the first k queue pairs, for k = 0, step, 2*step ... len(queue).

    order    : "ev" (queue order) or "random" (same pairs, shuffled; shows what the ranking is worth)
    accuracy : probability that the reviewer's verdict equals the truth (1.0 = a perfect reviewer)
    A reviewer judging "match" adds the pair; judging "non-match" on a pair that was auto-merged removes it.
    """
    auto = set(zip(out.pairs.a_id[out.pairs.decision == MERGE], out.pairs.b_id[out.pairs.decision == MERGE]))
    items = list(zip(queue.a_id, queue.b_id))
    rng = random.Random(seed)
    if order == "random":
        rng.shuffle(items)
    verdict = {}
    for a, b in items:
        truth = entity_of[a] == entity_of[b]
        verdict[(a, b)] = truth if rng.random() < accuracy else not truth
    marks = sorted({*range(0, len(items) + 1, step), len(items)})
    rows = []
    for k in marks:
        accepted = set(auto)
        for pair in items[:k]:
            if verdict[pair]:
                accepted.add(pair)
            else:
                accepted.discard(pair)
        m = evaluate_clusters(ids, entity_of, accepted)
        rows.append({"reviewed": k, "precision": m["precision"], "recall": m["recall"], "f1": m["f1"],
                     "bcubed_f1": m["bcubed_f1"], "over_merged_clusters": m["over_merged_clusters"]})  # fmt: skip
    return pd.DataFrame(rows).assign(order=order, reviewer_accuracy=accuracy)
