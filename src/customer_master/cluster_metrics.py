"""Cluster-level metrics: B-cubed precision/recall/F1 and cluster purity.

Pair-level recall counts every within-customer pair, so a 3-record customer carries 3 pairs and a 2-record customer 1.
Splitting one 3-record customer therefore costs up to 3 pairs, while a missed 2-record customer costs 1.
B-cubed scores every RECORD once (how pure is its predicted cluster, how much of its true customer did it recover),
so it does not over-weight large customers.
"""

from __future__ import annotations

from collections import Counter
from typing import Mapping


def bcubed(pred_of: Mapping[str, str], true_of: Mapping[str, str]) -> dict:
    """B-cubed precision, recall and F1 plus cluster purity.

    pred_of / true_of map record_id -> predicted cluster label / true entity id (same keys).
    """
    if set(pred_of) != set(true_of):
        raise ValueError("predicted and true labelings cover different records")
    n = len(pred_of)
    if n == 0:
        return {"bcubed_precision": 1.0, "bcubed_recall": 1.0, "bcubed_f1": 1.0, "purity": 1.0}
    joint = Counter((pred_of[r], true_of[r]) for r in pred_of)
    pred_size = Counter(pred_of.values())
    true_size = Counter(true_of.values())
    # sum over records of |P(r) ∩ T(r)| / |P(r)|; every record in cell (p, t) contributes joint[p,t] / size(p)
    p_sum = sum(c * c / pred_size[p] for (p, _), c in joint.items())
    r_sum = sum(c * c / true_size[t] for (_, t), c in joint.items())
    precision, recall = p_sum / n, r_sum / n
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    best: dict[str, int] = {}
    for (p, _), c in joint.items():
        best[p] = max(best.get(p, 0), c)
    return {
        "bcubed_precision": precision,
        "bcubed_recall": recall,
        "bcubed_f1": f1,
        "purity": sum(best.values()) / n,
    }
