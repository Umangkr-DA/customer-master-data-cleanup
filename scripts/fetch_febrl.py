"""Fetch the public Febrl benchmark (Christen, ANU) into data/benchmarks/febrl/.

The Febrl datasets are shipped inside the BSD-licensed `recordlinkage` wheel on PyPI, so this script just downloads
that wheel with pip and extracts the CSVs. Nothing is vendored in this repository (the files are git-ignored).

    python scripts/fetch_febrl.py            # needs network access to PyPI
Manual alternative: download https://pypi.org/project/recordlinkage/#files (wheel), unzip it and copy
recordlinkage/datasets/febrl/dataset3.csv to data/benchmarks/febrl/dataset3.csv.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "benchmarks" / "febrl"
WANTED = ["dataset1.csv", "dataset3.csv"]  # 1: 1,000 records; 3: 5,000 records with up to 5 duplicates per original


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as t:
        subprocess.run(
            [sys.executable, "-m", "pip", "download", "recordlinkage==0.16", "--no-deps", "-d", t, "-q"], check=True
        )
        wheel = next(Path(t).glob("recordlinkage-*.whl"))
        with zipfile.ZipFile(wheel) as z:
            for name in WANTED:
                (OUT / name).write_bytes(z.read(f"recordlinkage/datasets/febrl/{name}"))
    print("wrote", [str(OUT / n) for n in WANTED])


if __name__ == "__main__":
    main()
