"""Adapter for the public Febrl benchmark (Christen, ANU). See scripts/fetch_febrl.py for the download.

Febrl records have no phone or email; the FEBRL schema compares given name, surname, social-security id, date of
birth, street address, suburb, postcode and state. True entities are encoded in the record id
("rec-552-org", "rec-552-dup-3" -> entity 552). Nothing here reads that encoding except load_truth-style evaluation.
"""

from __future__ import annotations

import re

import pandas as pd

from customer_master.common import PROJECT_ROOT
from customer_master.datasets import Dataset

FEBRL_DIR = PROJECT_ROOT / "data" / "benchmarks" / "febrl"
FEBRL_COLUMNS = [
    "given_name", "surname", "street_number", "address_1", "address_2", "suburb", "postcode", "state",
    "date_of_birth", "soc_sec_id",
]  # fmt: skip


def load_febrl(number: int = 3) -> Dataset:
    path = FEBRL_DIR / f"dataset{number}.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found - run `python scripts/fetch_febrl.py` (needs network access to PyPI)"
        )
    df = pd.read_csv(path, dtype=str, keep_default_na=False, skipinitialspace=True)
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns={"rec_id": "record_id"})
    df["record_id"] = df["record_id"].str.strip()
    for col in FEBRL_COLUMNS:
        df[col] = df[col].str.strip().str.lower().replace("", None)
    df["source_system"] = "FEBRL"
    clean = df[["record_id", "source_system", *FEBRL_COLUMNS]]
    clean = clean.astype(object).where(clean.notna(), None)
    entity = clean.record_id.map(lambda r: re.match(r"rec-(\d+)-", r).group(1))
    truth = pd.DataFrame({"record_id": clean.record_id, "entity_id": entity})
    return Dataset(f"febrl{number}", df, clean, None, truth)
