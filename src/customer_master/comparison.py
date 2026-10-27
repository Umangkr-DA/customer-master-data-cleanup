"""Phase 2 workflow: tune on DEV only, freeze, then evaluate once on held-out.

tune_on_dev()      grid over matcher variants, thresholds calibrated on dev with the rules in
                   experiments.calibrate_thresholds; returns the frozen matchers and the full tuning table.
"""

from __future__ import annotations

import itertools

import pandas as pd

from customer_master.datasets import Dataset
from customer_master.experiments import calibrate_thresholds, run_matcher
from customer_master.matchers.exact import ExactKeyMatcher
from customer_master.matchers.fellegi_sunter import FellegiSunterMatcher
from customer_master.matchers.fuzzy import FuzzyRuleMatcher
from customer_master.schema import CUSTOMER, Schema

MIN_DEV_PRECISION = 0.97


def _calibrated(factory, ds: Dataset):
    m = factory()
    out, _ = run_matcher(m, ds)
    cal = calibrate_thresholds(out, ds, min_precision=MIN_DEV_PRECISION)
    m = factory()
    m.t_merge, m.t_review = cal["t_merge"], cal["t_review"]
    out, row = run_matcher(m, ds)
    return m, out, row


def tune_on_dev(ds: Dataset, schema: Schema = CUSTOMER, fs_grid: bool = True):
    """Returns (frozen {name: matcher}, tuning table). Only `ds` (dev data) is ever touched."""
    rows = []
    best_fs = best_fuzzy = None
    variants = [("fuzzy-rules", {}, lambda: FuzzyRuleMatcher(schema))]
    policies = ["phone+email", "review", "none"] if fs_grid else ["review"]
    for em_mode, policy, veto in itertools.product(["joint", "split"], policies, [True, False]):
        if not fs_grid and (em_mode, veto) != ("joint", True):
            continue
        kw = {"em_mode": em_mode, "name_missing_policy": policy, "conflict_veto": veto}
        variants.append(("fellegi-sunter", kw, lambda kw=kw: FellegiSunterMatcher(schema, **kw)))
    for name, kw, factory in variants:
        m, out, row = _calibrated(factory, ds)
        rec = {
            "matcher": name,
            **kw,
            "t_merge": round(m.t_merge, 3),
            "t_review": round(m.t_review, 3),
            "dev_precision": row["precision"],
            "dev_recall": row["recall"],
            "dev_f1": row["f1"],
            "over_merged_clusters": row["over_merged_clusters"],
            "review_pairs": int((out.pairs.decision == "review").sum()),
        }
        rows.append(rec)
        ok = row["precision"] >= MIN_DEV_PRECISION
        if name == "fuzzy-rules" and ok and (best_fuzzy is None or row["f1"] > best_fuzzy[1]):
            best_fuzzy = (m, row["f1"])
        if name == "fellegi-sunter" and ok and (best_fs is None or row["f1"] > best_fs[1]):
            best_fs = (m, row["f1"])
    table = pd.DataFrame(rows)
    frozen = {"exact-key": ExactKeyMatcher()}
    if best_fuzzy:
        frozen["fuzzy-rules"] = best_fuzzy[0]
    if best_fs:
        frozen["fellegi-sunter"] = best_fs[0]
    return frozen, table
