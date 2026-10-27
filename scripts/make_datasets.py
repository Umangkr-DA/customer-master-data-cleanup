"""Create the hard-mode DEV and HELD-OUT datasets (and, optionally, extra seeds for multi-seed runs).

python scripts/make_datasets.py            # data/dev (seed 101) and data/heldout (seed 202)
python scripts/make_datasets.py --check    # fail if committed files differ from regenerated ones
"""

from __future__ import annotations

import argparse
import filecmp
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from customer_master.generate_hard import generate_hard  # noqa: E402

SPLITS = {"dev": 101, "heldout": 202}
FILES = ["crm.csv", "web.csv", "sales.csv", "answer_key.csv", "generation_config.json"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify committed datasets are reproducible")
    args = ap.parse_args()
    for name, seed in SPLITS.items():
        target = ROOT / "data" / name
        if args.check:
            with tempfile.TemporaryDirectory() as t:
                generate_hard(Path(t), seed=seed)
                bad = [f for f in FILES if not filecmp.cmp(Path(t) / f, target / f, shallow=False)]
            if bad:
                sys.exit(f"data/{name} differs from regenerated data: {bad}")
            print(f"data/{name}: reproducible")
        else:
            cfg = generate_hard(target, seed=seed)
            print(f"wrote data/{name} (seed {cfg.seed})")


if __name__ == "__main__":
    main()
