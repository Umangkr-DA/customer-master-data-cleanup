"""Multi-seed evaluation: frozen matcher configurations on freshly generated hard-mode datasets.

Thresholds and rules are frozen on DEV (see comparison.tune_on_dev); each seed is a new dataset the configs have never
seen. Fellegi-Sunter m/u parameters are re-estimated per dataset by EM, which uses no labels. The answer key is read
only by run_matcher().
"""

from __future__ import annotations

import math

import pandas as pd

from customer_master.datasets import load_dataset
from customer_master.experiments import run_matcher

DEFAULT_SEEDS = tuple(range(1001, 1021))  # 20 seeds, disjoint from dev (101) and held-out (202)
METRICS = ["precision", "recall", "f1", "bcubed_precision", "bcubed_recall", "bcubed_f1"]

# two-sided 95% Student-t critical values by degrees of freedom (avoids a scipy dependency)
_T95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
}  # fmt: skip


def t_crit(df: int) -> float:
    return _T95.get(df, 1.96 if df > 30 else float("nan"))


def run_multiseed(frozen: dict, seeds=DEFAULT_SEEDS) -> pd.DataFrame:
    """One row per (seed, matcher) with pair- and cluster-level metrics."""
    rows = []
    for seed in seeds:
        ds = load_dataset(f"seed-{seed}")
        for name, matcher in frozen.items():
            _, m = run_matcher(matcher, ds)
            rows.append({"seed": seed, "matcher": name, **{k: m[k] for k in METRICS},
                         "candidate_pairs": m["candidate_pairs"], "runtime_s": m["runtime_s"],
                         "over_merged_clusters": m["over_merged_clusters"]})  # fmt: skip
    return pd.DataFrame(rows)


def summarize(per_seed: pd.DataFrame) -> pd.DataFrame:
    """Mean, sample std and 95% t-interval of the mean for each matcher and metric."""
    out = []
    for name, g in per_seed.groupby("matcher", sort=False):
        n = len(g)
        for metric in METRICS:
            x = g[metric].astype(float)
            sd = float(x.std(ddof=1)) if n > 1 else float("nan")
            half = t_crit(n - 1) * sd / math.sqrt(n) if n > 1 else float("nan")
            out.append({"matcher": name, "metric": metric, "n_seeds": n, "mean": float(x.mean()), "std": sd,
                        "ci95_low": float(x.mean()) - half, "ci95_high": float(x.mean()) + half})  # fmt: skip
    return pd.DataFrame(out)
