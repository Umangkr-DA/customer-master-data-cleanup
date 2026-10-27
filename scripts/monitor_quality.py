"""Track the six rule pass rates per pipeline run and flag drops.

    python scripts/monitor_quality.py [--run-id ID]

Reads outputs/validation_summary.csv (written by run_pipeline.py), appends a row to outputs/dq_history.csv,
draws docs/dq_history.png, and exits 1 if any rule dropped by more than max_drop_pp (config/monitoring.json).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from customer_master.monitoring import append_run, current_rates, find_drops, load_settings, plot_history  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()
    cfg = load_settings(ROOT / "config" / "monitoring.json")
    val = pd.read_csv(ROOT / "outputs" / "validation_summary.csv")
    rates = current_rates(val, cfg["stage"], cfg["rules"])
    records = int(val.set_index("rule_id").loc["ALL", f"{cfg['stage']}_total"])
    hist = append_run(ROOT / "outputs" / "dq_history.csv", rates, records, cfg["stage"], args.run_id)
    plot_history(hist, cfg["rules"], ROOT / "docs" / "dq_history.png")
    drops = find_drops(hist, cfg["rules"], cfg["max_drop_pp"])
    print(f"{len(hist)} runs in history; latest: " + ", ".join(f"{r} {rates[r]:.1%}" for r in cfg["rules"]))
    for d in drops:
        print(f"ALERT {d['rule']}: {d['previous']:.1%} -> {d['latest']:.1%} ({d['change_pp']:+.2f} pp)")
    return 1 if drops else 0


if __name__ == "__main__":
    sys.exit(main())
