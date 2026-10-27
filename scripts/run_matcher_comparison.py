"""Phase 2: matcher comparison on dev-hard and held-out (+ the Febrl benchmark when its data is present).

    python scripts/run_matcher_comparison.py

1. Tune on DEV only (variants x calibrated thresholds)            -> outputs/tuning_dev.csv
2. Freeze the chosen configurations (hash recorded in the ledger)
3. Evaluate each frozen config on dev and, once, on held-out       -> outputs/matcher_comparison.csv
4. Febrl: calibrate on dataset1, report dataset3                   -> outputs/febrl_comparison.csv
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from customer_master.comparison import tune_on_dev  # noqa: E402
from customer_master.common import OUTPUT_DIR  # noqa: E402
from customer_master.datasets import load_dataset  # noqa: E402
from customer_master.experiments import metrics_table, run_matcher  # noqa: E402
from customer_master.febrl import FEBRL_DIR, load_febrl  # noqa: E402
from customer_master.heldout import config_hash, evaluate_frozen  # noqa: E402
from customer_master.schema import FEBRL  # noqa: E402


def main() -> None:
    dev = load_dataset("dev")
    frozen, tuning = tune_on_dev(dev)
    tuning.to_csv(OUTPUT_DIR / "tuning_dev.csv", index=False)
    print(tuning.to_string(index=False))

    rows, params = [], {}
    for name, matcher in frozen.items():
        out, dev_row = run_matcher(matcher, dev)
        dev_row["review_pairs"] = int((out.pairs.decision == "review").sum())
        dev_row["config_hash"] = config_hash(matcher.config())
        rows.append(dev_row)
        if "parameters" in out.info:
            params[name] = out.info["parameters"]
        held = dict(evaluate_frozen(matcher))
        held["config_hash"] = config_hash(matcher.config())
        rows.append(held)
    table = metrics_table(rows).assign(config_hash=[r["config_hash"] for r in rows])
    table.to_csv(OUTPUT_DIR / "matcher_comparison.csv", index=False)
    (OUTPUT_DIR / "fs_parameters_dev.json").write_text(json.dumps(params, indent=2) + "\n", encoding="utf-8")
    print(table.drop(columns="config_hash").to_string(index=False))

    if (FEBRL_DIR / "dataset3.csv").exists():
        f1_, f3_ = load_febrl(1), load_febrl(3)
        febrl_frozen, febrl_tuning = tune_on_dev(f1_, schema=FEBRL, fs_grid=False)
        febrl_tuning.to_csv(OUTPUT_DIR / "febrl_tuning_dataset1.csv", index=False)
        frows = []
        for name, matcher in febrl_frozen.items():
            if name == "exact-key":
                continue  # needs phone/email columns that Febrl does not have
            frows.append(run_matcher(matcher, f3_)[1])
        pd.DataFrame(frows).to_csv(OUTPUT_DIR / "febrl_comparison.csv", index=False)
        print(metrics_table(frows).to_string(index=False))
    else:
        print("Febrl data not found - run scripts/fetch_febrl.py to add the external benchmark")


if __name__ == "__main__":
    main()
