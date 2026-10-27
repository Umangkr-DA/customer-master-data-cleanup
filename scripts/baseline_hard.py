"""Phase 1 report: current exact-key rules on the default, dev-hard and held-out datasets.

Writes outputs/baseline_hard.csv. The held-out row goes through the frozen-config ledger (evaluated once).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from customer_master.common import OUTPUT_DIR  # noqa: E402
from customer_master.datasets import load_dataset  # noqa: E402
from customer_master.experiments import metrics_table, run_matcher  # noqa: E402
from customer_master.heldout import evaluate_frozen  # noqa: E402
from customer_master.matchers.exact import ExactKeyMatcher  # noqa: E402


def main() -> None:
    m = ExactKeyMatcher()
    rows = [run_matcher(m, load_dataset(n))[1] for n in ("default", "dev")]
    rows.append(evaluate_frozen(m))
    table = metrics_table(rows)
    table.to_csv(OUTPUT_DIR / "baseline_hard.csv", index=False)
    print(table.to_string(index=False))


if __name__ == "__main__":
    main()
