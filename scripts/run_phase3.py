"""Phase 3: precision-recall curves (dev), 20-seed evaluation with 95% intervals.

    python scripts/run_phase3.py [--seeds N]

Matcher configurations are frozen on DEV (comparison.tune_on_dev). Seeds 1001.. are fresh hard-mode datasets the
configurations have never seen; nothing is tuned on them.
Outputs: outputs/pr_curve_dev.csv, docs/pr_curve.png, outputs/multiseed_per_seed.csv, outputs/multiseed_summary.csv,
         docs/multiseed_f1.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from customer_master.common import OUTPUT_DIR  # noqa: E402
from customer_master.comparison import tune_on_dev  # noqa: E402
from customer_master.datasets import load_dataset  # noqa: E402
from customer_master.experiments import pr_curve, run_matcher  # noqa: E402
from customer_master.heldout import config_hash, read_ledger  # noqa: E402
from customer_master.multiseed import DEFAULT_SEEDS, run_multiseed, summarize  # noqa: E402

DOCS = ROOT / "docs"
COLORS = {"exact-key": "#7a7a7a", "fuzzy-rules": "#1f77b4", "fellegi-sunter": "#d95f02"}


def plot_pr(curves: dict, frozen_points: dict, heldout_points: dict) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 5))
    for name, c in curves.items():
        if len(c) > 20:  # continuous score: draw the curve
            ax.plot(c.recall, c.precision, label=f"{name} (dev curve)", color=COLORS[name], lw=1.8)
        else:  # only a handful of distinct scores: show the operating points, do not imply a continuum
            ax.scatter(c.recall, c.precision, label=f"{name} (dev, {len(c)} possible thresholds)",
                       color=COLORS[name], marker="s", s=28, alpha=0.8)  # fmt: skip
        if name in frozen_points:
            r, p = frozen_points[name]
            ax.scatter([r], [p], color=COLORS[name], marker="o", s=55, zorder=3)
        if name in heldout_points:
            r, p = heldout_points[name]
            ax.scatter([r], [p], color=COLORS[name], marker="X", s=70, zorder=3, edgecolor="black", linewidth=0.6)
    ax.scatter([], [], color="black", marker="o", label="frozen threshold, dev")
    ax.scatter([], [], color="black", marker="X", label="frozen threshold, held-out (single run)")
    ax.set_xlabel("pair recall")
    ax.set_ylabel("pair precision")
    ax.set_xlim(0, 1)
    ax.set_ylim(0.5, 1.005)
    ax.grid(alpha=0.3)
    ax.legend(loc="lower left", fontsize=8)
    ax.set_title("Precision-recall over the merge threshold (hard-mode dev data)")
    fig.tight_layout()
    fig.savefig(DOCS / "pr_curve.png", dpi=150)
    plt.close(fig)


def plot_multiseed(per_seed: pd.DataFrame, summary: pd.DataFrame) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 4))
    names = list(per_seed.matcher.unique())
    for i, name in enumerate(names):
        g = per_seed[per_seed.matcher == name]
        ax.scatter([i] * len(g), g.f1, color=COLORS[name], alpha=0.35, s=18)
        s = summary[(summary.matcher == name) & (summary.metric == "f1")].iloc[0]
        ax.errorbar([i], [s["mean"]], yerr=[[s["mean"] - s.ci95_low], [s.ci95_high - s["mean"]]], color="black",
                    capsize=6, marker="D", ms=5)  # fmt: skip
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names)
    ax.set_ylabel("pair F1")
    ax.set_title(f"Pair F1 over {per_seed.seed.nunique()} unseen seeds (diamond = mean, bar = 95% interval)")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(DOCS / "multiseed_f1.png", dpi=150)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=len(DEFAULT_SEEDS))
    args = ap.parse_args()
    seeds = tuple(range(1001, 1001 + args.seeds))
    dev = load_dataset("dev")
    frozen, _ = tune_on_dev(dev)
    ledger = read_ledger()

    curves, frozen_pts, held_pts, rows = {}, {}, {}, []
    for name, matcher in frozen.items():
        out, row = run_matcher(matcher, dev)
        curve = pr_curve(out, dev)
        curve.insert(0, "matcher", name)
        rows.append(curve)
        curves[name] = curve
        frozen_pts[name] = (row["recall"], row["precision"])
        h = config_hash(matcher.config())
        if h in ledger:  # recorded single held-out run of the SAME frozen config; nothing is re-evaluated here
            m = ledger[h]["metrics"]
            held_pts[name] = (m["recall"], m["precision"])
    pd.concat(rows).to_csv(OUTPUT_DIR / "pr_curve_dev.csv", index=False)
    plot_pr(curves, frozen_pts, held_pts)

    per_seed = run_multiseed(frozen, seeds)
    summary = summarize(per_seed)
    per_seed.to_csv(OUTPUT_DIR / "multiseed_per_seed.csv", index=False)
    summary.to_csv(OUTPUT_DIR / "multiseed_summary.csv", index=False)
    plot_multiseed(per_seed, summary)
    show = summary[summary.metric.isin(["precision", "recall", "f1", "bcubed_f1"])]
    print(show.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
