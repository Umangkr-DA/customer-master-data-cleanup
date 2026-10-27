"""Experiment harness: run a matcher on a dataset and score it against the answer key.

This is the only place (besides evaluation) where the answer key is opened.
"""

from __future__ import annotations

import pandas as pd

from customer_master.datasets import Dataset, load_truth
from customer_master.evaluation import evaluate_clusters, pairs_within
from customer_master.matchers.base import MatchOutput


def run_matcher(matcher, ds: Dataset) -> tuple[MatchOutput, dict]:
    """Match on standardized records, then evaluate. Returns (output, metrics row)."""
    out = matcher.match(ds.clean)
    truth = load_truth(ds)
    if set(truth.record_id) != set(ds.clean.record_id):
        raise ValueError("answer key and records cover different record ids")
    entity_of = dict(zip(truth.record_id, truth.entity_id))
    m = evaluate_clusters(list(ds.clean.record_id), entity_of, out.merged())
    m["pair_completeness"] = pair_completeness(out, entity_of)
    m.update(
        matcher=matcher.name,
        dataset=ds.name,
        records=len(ds.clean),
        candidate_pairs=out.n_candidates,
        runtime_s=round(out.runtime_s, 3),
    )
    return out, m


def pair_completeness(out: MatchOutput, entity_of: dict) -> float:
    """Blocking recall: share of true duplicate pairs that candidate generation proposed at all (an upper bound on
    pair recall for any scoring rule)."""
    groups: dict = {}
    for rid, ent in entity_of.items():
        groups.setdefault(ent, []).append(rid)
    true_pairs = pairs_within(groups)
    cand = set(zip(out.pairs.a_id, out.pairs.b_id))
    return len(true_pairs & cand) / len(true_pairs) if true_pairs else 1.0


def metrics_table(rows: list[dict]) -> pd.DataFrame:
    cols = [
        "matcher", "dataset", "records", "precision", "recall", "f1", "true_positive_pairs",
        "false_positive_pairs", "false_negative_pairs", "bcubed_precision", "bcubed_recall", "bcubed_f1", "purity",
        "over_merged_clusters", "pair_completeness", "candidate_pairs", "runtime_s",
    ]  # fmt: skip
    return pd.DataFrame(rows)[cols]


def entity_map(ds: Dataset) -> dict:
    truth = load_truth(ds)
    return dict(zip(truth.record_id, truth.entity_id))


def pr_curve(out: MatchOutput, ds: Dataset, thresholds=None) -> pd.DataFrame:
    """Pair-level precision/recall/F1 (after transitive clustering) as the merge threshold moves.

    Thresholds default to ~80 quantiles of the observed scores. Guards (name conflict / blank-name policy) stay on.
    """
    import numpy as np

    entity_of = entity_map(ds)
    ids = list(ds.clean.record_id)
    if thresholds is None:
        scores = out.pairs.score[out.pairs.guard_ok]
        thresholds = sorted(set(np.round(np.quantile(scores, np.linspace(0, 1, 81)), 4))) if len(scores) else []
    rows = []
    for t in thresholds:
        m = evaluate_clusters(ids, entity_of, out.merged_at(t))
        rows.append(
            {
                "threshold": float(t),
                "precision": m["precision"],
                "recall": m["recall"],
                "f1": m["f1"],
                "merged_pairs": len(out.merged_at(t)),
                "over_merged_clusters": m["over_merged_clusters"],
            }
        )
    return pd.DataFrame(rows)


def calibrate_thresholds(
    out: MatchOutput, ds: Dataset, min_precision: float = 0.97, min_review_precision: float = 0.25
) -> dict:
    """Pick (t_merge, t_review) on DEV data with two stated rules.

    t_merge  : the LOWEST merge threshold whose dev pair precision is >= min_precision (maximises recall under a
               precision floor - "blank is better than wrong").
    t_review : the lowest threshold below t_merge such that at least `min_review_precision` of the pairs in the review
               band [t_review, t_merge) are truly the same customer (a reviewer sees at least 1 true duplicate in 4).
    """
    curve = pr_curve(out, ds)
    ok = curve[curve.precision >= min_precision]
    if ok.empty:
        t_merge = float(curve.threshold.max()) + 1e-6
    else:
        t_merge = float(ok.threshold.min())
    entity_of = entity_map(ds)
    pairs = out.pairs[out.pairs.score < t_merge].copy()
    pairs["true"] = [entity_of[a] == entity_of[b] for a, b in zip(pairs.a_id, pairs.b_id)]
    pairs = pairs.sort_values("score", ascending=False)
    t_review = t_merge
    if len(pairs):
        hits = pairs["true"].cumsum().to_numpy()
        n = range(1, len(pairs) + 1)
        prec = [h / k for h, k in zip(hits, n)]
        best = [k for k, pr in enumerate(prec) if pr >= min_review_precision]
        if best:
            t_review = float(pairs.score.iloc[best[-1]])
    return {"t_merge": t_merge, "t_review": min(t_review, t_merge)}
