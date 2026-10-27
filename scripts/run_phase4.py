"""Phase 4: review queue + reviewer simulation (the frozen Fellegi-Sunter configuration).

    python scripts/run_phase4.py

outputs/review_queue.csv          the ranked queue for the DEV data (a production artefact: no answer key is used)
outputs/review_simulation.csv     SIMULATED reviewer on dev: ranked vs random order, perfect vs 95% accurate reviewer
outputs/review_simulation_seeds.csv/_summary.csv   perfect reviewer, ranked order, 20 unseen seeds (mean, 95% interval)
docs/review_simulation.png
The answer key is used only to play the reviewer and to score the result; labelled as a simulation everywhere.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from customer_master.common import OUTPUT_DIR  # noqa: E402
from customer_master.comparison import tune_on_dev  # noqa: E402
from customer_master.datasets import load_dataset  # noqa: E402
from customer_master.evaluation_sim import simulate_review  # noqa: E402
from customer_master.experiments import entity_map, run_matcher  # noqa: E402
from customer_master.multiseed import DEFAULT_SEEDS, t_crit  # noqa: E402
from customer_master.review import RANKINGS, build_review_queue  # noqa: E402

STEP = 50
CHECKPOINTS = [0, 100, 250, 500, 1000]


def plot(sim: pd.DataFrame, n_queue: int) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), sharex=True)
    styles = {
        ("ev", 1.0): ("#d95f02", "-", "ranked by expected value, perfect reviewer"),
        ("random", 1.0): ("#7a7a7a", "--", "random order, perfect reviewer"),
        ("ev", 0.95): ("#1f77b4", "-", "ranked, 95%-accurate reviewer"),
    }
    for (order, acc), (color, ls, label) in styles.items():
        g = sim[(sim.order == order) & (sim.reviewer_accuracy == acc)]
        axes[0].plot(g.reviewed, g.recall, color=color, ls=ls, label=label)
        axes[1].plot(g.reviewed, g.precision, color=color, ls=ls, label=label)
    axes[0].set_ylabel("pair recall")
    axes[1].set_ylabel("pair precision")
    for ax in axes:
        ax.set_xlabel("pairs reviewed (SIMULATED reviewer)")
        ax.grid(alpha=0.3)
    axes[0].legend(fontsize=8, loc="lower right")
    fig.suptitle(f"Review simulation on dev data, queue of {n_queue} pairs (answer key plays the reviewer)")
    fig.tight_layout()
    fig.savefig(ROOT / "docs" / "review_simulation.png", dpi=150)
    plt.close(fig)


def main() -> None:
    dev = load_dataset("dev")
    frozen, _ = tune_on_dev(dev)
    matcher = frozen["fellegi-sunter"]
    out, _ = run_matcher(matcher, dev)
    ids, entity = list(dev.clean.record_id), entity_map(dev)

    # choose the ranking on DEV: highest simulated recall after 250 reviews (a perfect reviewer)
    variants = []
    for ranking in RANKINGS:
        qv = build_review_queue(dev.clean, out, ranking=ranking)
        sv = simulate_review(ids, entity, out, qv, "ev", 1.0, step=STEP).set_index("reviewed")
        hits = [entity[a] == entity[b] for a, b in zip(qv.a_id, qv.b_id)]
        variants.append(
            {
                "ranking": ranking,
                "queue_size": len(qv),
                "true_share_top100": sum(hits[:100]) / min(100, len(hits)),
                "recall_after_100": sv.recall.loc[:100].iloc[-1],
                "recall_after_250": sv.recall.loc[:250].iloc[-1],
                "recall_after_500": sv.recall.loc[:500].iloc[-1],
            }
        )
    rank_table = pd.DataFrame(variants)
    rank_table["chosen_on_dev"] = rank_table.recall_after_250 == rank_table.recall_after_250.max()
    rank_table.to_csv(OUTPUT_DIR / "review_ranking_variants_dev.csv", index=False)
    print(rank_table.round(4).to_string(index=False))
    chosen = rank_table[rank_table.chosen_on_dev].ranking.iloc[0]
    queue = build_review_queue(dev.clean, out, ranking=chosen)
    queue.to_csv(OUTPUT_DIR / "review_queue.csv", index=False)

    print(f"ranking chosen on dev: {chosen}")
    sims = [
        simulate_review(ids, entity, out, queue, "ev", 1.0, step=STEP),
        simulate_review(ids, entity, out, queue, "random", 1.0, seed=1, step=STEP),
        simulate_review(ids, entity, out, queue, "ev", 0.95, seed=1, step=STEP),
    ]
    sim = pd.concat(sims)
    sim.to_csv(OUTPUT_DIR / "review_simulation.csv", index=False)
    plot(sim, len(queue))
    truth = [entity[a] == entity[b] for a, b in zip(queue.a_id, queue.b_id)]
    comp = (
        queue.assign(true_match=truth, name_conflict=queue.disagree_fields.fillna("").str.contains("first|last"))
        .groupby(["name_conflict", "reason_in_queue"], as_index=False)
        .agg(pairs=("true_match", "size"), true_matches=("true_match", "sum"))
    )
    comp["share_true"] = comp.true_matches / comp.pairs
    comp.to_csv(OUTPUT_DIR / "review_queue_composition_dev.csv", index=False)  # evaluation view: uses the answer key
    print(comp.round(3).to_string(index=False))
    top = truth[:100]
    print(
        f"queue={len(queue)} pairs, true matches in queue={sum(truth)} ({sum(truth) / len(truth):.1%}), "
        f"top-100 true share={sum(top) / len(top):.1%}"
    )
    print(sim[(sim.reviewed.isin(CHECKPOINTS)) | (sim.reviewed == len(queue))].round(4).to_string(index=False))

    rows = []
    for seed in DEFAULT_SEEDS:
        ds = load_dataset(f"seed-{seed}")
        o, _ = run_matcher(matcher, ds)
        q = build_review_queue(ds.clean, o, ranking=chosen)
        s = simulate_review(list(ds.clean.record_id), entity_map(ds), o, q, "ev", 1.0, step=STEP)
        for k in CHECKPOINTS + ["all"]:
            row = s.iloc[-1] if k == "all" else s[s.reviewed <= k].iloc[-1]
            rows.append({"seed": seed, "reviewed": k, "queue_size": len(q), "precision": row.precision,
                         "recall": row.recall, "f1": row.f1})  # fmt: skip
    per = pd.DataFrame(rows)
    per.to_csv(OUTPUT_DIR / "review_simulation_seeds.csv", index=False)
    summ = []
    for k, g in per.groupby("reviewed", sort=False):
        n = len(g)
        for metric in ["precision", "recall", "f1"]:
            mean, sd = g[metric].mean(), g[metric].std(ddof=1)
            half = t_crit(n - 1) * sd / n**0.5
            summ.append({"reviewed": k, "metric": metric, "n_seeds": n, "mean": mean, "ci95_low": mean - half,
                         "ci95_high": mean + half, "mean_queue_size": g.queue_size.mean()})  # fmt: skip
    summary = pd.DataFrame(summ)
    summary.to_csv(OUTPUT_DIR / "review_simulation_seeds_summary.csv", index=False)
    print(summary[summary.metric.isin(["recall", "precision"])].round(4).to_string(index=False))


if __name__ == "__main__":
    main()
