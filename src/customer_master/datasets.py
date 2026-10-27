"""Dataset registry and loaders for experiments.

Datasets
  default  data/raw                  original easy synthetic data (seed 42), used by the main pipeline
  dev      data/dev                  hard mode, seed 101  -> the ONLY data used to design and tune matchers
  heldout  data/heldout              hard mode, seed 202  -> evaluated once per frozen configuration
  seed-N   generated on the fly      hard mode, seed N    -> multi-seed evaluation (never written to data/)

Leakage control: load_dataset() returns records only. The answer key is read by load_truth(), which is called
exclusively from evaluation / experiment code. tests/test_no_leakage.py checks that no matcher, standardizer or
survivorship module references it.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from customer_master.common import GROUND_TRUTH_PATH, PROJECT_ROOT, RAW_DIR
from customer_master.profile import load_raw_sources
from customer_master.standardizers import standardize_record

DATA_DIR = PROJECT_ROOT / "data"
LOCATIONS = {
    "default": (RAW_DIR, GROUND_TRUTH_PATH),
    "dev": (DATA_DIR / "dev", DATA_DIR / "dev" / "answer_key.csv"),
    "heldout": (DATA_DIR / "heldout", DATA_DIR / "heldout" / "answer_key.csv"),
}
CLEAN_COLUMNS = ["record_id", "source_system", "full_name", "phone", "email", "city", "pincode", "signup_date"]


@dataclass
class Dataset:
    name: str
    raw: pd.DataFrame
    clean: pd.DataFrame  # standardized; unfixable values are None
    truth_path: Path | None = None  # evaluation code only
    truth_frame: pd.DataFrame | None = None  # evaluation code only (generated seeds)


def standardize_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """Apply the (unchanged) deterministic standardizers to every record."""
    rows = []
    for rec in raw.to_dict("records"):
        rows.append({"record_id": rec["record_id"], "source_system": rec["source_system"], **standardize_record(rec)})
    clean = pd.DataFrame(rows, columns=CLEAN_COLUMNS)
    return clean.astype(object).where(clean.notna(), None)


def load_dataset(name: str, allow_heldout: bool = False) -> Dataset:
    """Load and standardize a dataset. The held-out set needs allow_heldout=True so it cannot be used by accident."""
    if name == "heldout" and not allow_heldout:
        raise PermissionError(
            "held-out data may only be loaded by the frozen-configuration evaluator (allow_heldout=True)"
        )
    if name.startswith("seed-"):
        from customer_master.generate_hard import generate_hard

        seed = int(name.split("-", 1)[1])
        with tempfile.TemporaryDirectory() as t:
            generate_hard(Path(t), seed=seed)
            raw = load_raw_sources(Path(t))
            truth = pd.read_csv(Path(t) / "answer_key.csv", dtype=str)
        return Dataset(name, raw, standardize_frame(raw), None, truth)
    raw_dir, truth_path = LOCATIONS[name]
    raw = load_raw_sources(raw_dir)
    return Dataset(name, raw, standardize_frame(raw), truth_path)


def load_truth(ds: Dataset) -> pd.DataFrame:
    """Answer key (record_id, entity_id). EVALUATION ONLY."""
    if ds.truth_frame is not None:
        return ds.truth_frame
    return pd.read_csv(ds.truth_path, dtype=str)
