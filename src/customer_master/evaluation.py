"""Pure evaluation helpers: pair-level precision/recall/F1 and record-count reconciliation.

These functions take plain Python structures (no database, no files) so they can be unit tested.
The ground truth is only ever passed in here; it is never visible to the matching steps.
"""

from __future__ import annotations

from itertools import combinations
from typing import Iterable, Mapping


def pairs_within(groups: Mapping[str, Iterable[str]]) -> set[tuple[str, str]]:
    """All unordered record pairs inside each group (a group of n records gives n*(n-1)/2 pairs)."""
    pairs: set[tuple[str, str]] = set()
    for members in groups.values():
        pairs.update(combinations(sorted(members), 2))
    return pairs


def pair_metrics(true_pairs: set, predicted_pairs: set) -> dict:
    """Pair-level confusion counts, precision, recall and F1.

    Positive class = "this pair of records is the same customer".
    Precision/recall default to 1.0 when their denominator is 0 (nothing predicted / nothing to find).
    """
    tp = len(true_pairs & predicted_pairs)
    fp = len(predicted_pairs - true_pairs)
    fn = len(true_pairs - predicted_pairs)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "true_positive_pairs": tp,
        "false_positive_pairs": fp,
        "false_negative_pairs": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def reconcile_counts(raw_records: int, golden_records: int, true_customers: int, over_merged_clusters: int) -> dict:
    """Record-level reconciliation, kept separate from pair-level metrics.

    records_merged            = raw - golden           (rows removed by the pipeline)
    true_redundant_records    = raw - true customers   (rows that SHOULD be removed)
    unmerged_redundant_records = golden - true customers (duplicates the pipeline left behind)
    The last identity (true_redundant - merged == unmerged) only equals "missed duplicates" exactly
    when no cluster mixes different customers, so it is reported together with that condition.
    """
    if golden_records > raw_records:
        raise ValueError("golden_records cannot exceed raw_records")
    merged = raw_records - golden_records
    true_redundant = raw_records - true_customers
    return {
        "records_merged": merged,
        "true_redundant_records": true_redundant,
        "unmerged_redundant_records": golden_records - true_customers,
        "reconciles": over_merged_clusters == 0 and true_redundant - merged == golden_records - true_customers,
    }


def evaluate_clusters(record_ids, entity_of: Mapping[str, str], merged_pairs: Iterable[tuple[str, str]]) -> dict:
    """Pair-level metrics for a set of accepted pairs, after transitive clustering.

    Uses the SAME definition as the main pipeline: every within-cluster pair is predicted positive and every
    within-entity pair in the answer key is a true positive candidate. Also returns cluster-level counts.
    """
    from customer_master.clustering import cluster

    labels = cluster(record_ids, merged_pairs)
    pred_groups: dict[str, list[str]] = {}
    for rid, lab in labels.items():
        pred_groups.setdefault(lab, []).append(rid)
    true_groups: dict[str, list[str]] = {}
    for rid in labels:
        true_groups.setdefault(entity_of[rid], []).append(rid)
    pm = pair_metrics(pairs_within(true_groups), pairs_within(pred_groups))
    over_merged = sum(len({entity_of[r] for r in v}) > 1 for v in pred_groups.values())
    from customer_master.cluster_metrics import bcubed

    return {
        **pm,
        **bcubed(labels, {r: entity_of[r] for r in labels}),
        "predicted_pairs": pm["true_positive_pairs"] + pm["false_positive_pairs"],
        "true_pairs": pm["true_positive_pairs"] + pm["false_negative_pairs"],
        "true_customers": len(true_groups),
        "predicted_clusters": len(pred_groups),
        "over_merged_clusters": over_merged,
    }
