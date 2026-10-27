"""Run the whole project from the repository root:

    python scripts/run_pipeline.py [--regenerate]

Default      : uses the committed synthetic data in data/raw and runs steps 1-6.
--regenerate : first re-creates the synthetic data (seed 42; output is identical every time).
At the end the script checks that the headline numbers quoted in README.md match outputs/metrics.json.
Exit status is non-zero if any stage fails.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

STEPS = [
    ("customer_master.profile", "Load raw data + profile"),
    ("customer_master.standardize", "Standardize fields"),
    ("customer_master.matching", "Entity resolution"),
    ("customer_master.golden_record", "Golden records (survivorship)"),
    ("customer_master.validation", "Validation + evaluation"),
    ("customer_master.reporting", "Excel report"),
]
EXPECTED_OUTPUTS = [
    "outputs/golden_records.csv",
    "outputs/validation_summary.csv",
    "outputs/evaluation_summary.csv",
    "outputs/metrics.json",
    "reports/customer_data_quality_report.xlsx",
]


def check_readme_matches_metrics() -> None:
    """Fail if README.md quotes headline numbers that differ from what the pipeline just produced."""
    m = json.loads((ROOT / "outputs" / "metrics.json").read_text(encoding="utf-8"))
    v = m["validation_all_rules"]
    expected = [
        f"{m['raw_records']:,}",
        f"{m['golden_records']:,}",
        f"{m['records_merged']:,}",
        f"{m['precision']:.1%}",
        f"{m['recall']:.1%}",
        f"{m['f1']:.1%}",
        f"{v['raw']:.1%}",
        f"{v['standardized']:.1%}",
        f"{v['golden']:.1%}",
        f"{m['validation_like_for_like']['standardized_per_cluster']:.1%}",
        f"{m['validation_like_for_like']['merge_lift_pp_like_for_like']:.1f} pp",
    ]
    from customer_master import headline

    expected += headline.readme_tokens(headline.write())  # held-out, 20-seed intervals, review, scale, Febrl
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    missing = [e for e in expected if e not in readme]
    if missing:
        sys.exit(f"README.md does not contain current numbers: {missing}")
    print(f"README check passed: {len(expected)} headline checks match outputs/metrics.json and headline_metrics.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--regenerate", action="store_true", help="re-create the synthetic raw data first")
    args = parser.parse_args()

    steps = ([("customer_master.generate_data", "Generate synthetic data")] if args.regenerate else []) + STEPS
    start = time.time()
    for n, (module, label) in enumerate(steps, 1):
        print(f"\n=== [{n}/{len(steps)}] {label} ({module}) ===")
        try:
            importlib.import_module(module).main()
        except Exception as exc:  # report which stage failed, then exit non-zero
            sys.exit(f"FAILED at stage '{label}': {type(exc).__name__}: {exc}")
    missing = [p for p in EXPECTED_OUTPUTS if not (ROOT / p).exists()]
    if missing:
        sys.exit(f"Missing expected outputs: {missing}")
    check_readme_matches_metrics()
    print(f"\nPipeline finished in {time.time() - start:.1f}s")


if __name__ == "__main__":
    main()
