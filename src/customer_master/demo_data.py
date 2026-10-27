"""Data access for the optional Streamlit demo (kept free of any streamlit import so it can be tested)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .common import OUTPUT_DIR

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
SOURCE_FIELDS = ["full_name", "phone", "email", "city", "pincode", "signup_date"]
SURVIVOR_FIELDS = {
    "full_name": "name_source",
    "phone": "phone_source",
    "email": "email_source",
    "city": "address_source",
    "pincode": "address_source",
    "signup_date": "date_source",
}


def load_all(raw_dir: Path = RAW_DIR, out_dir: Path = OUTPUT_DIR) -> dict:
    raw = pd.concat([pd.read_csv(p, dtype=str) for p in sorted(Path(raw_dir).glob("*.csv"))], ignore_index=True)
    return {
        "raw": raw.set_index("record_id"),
        "golden": pd.read_csv(out_dir / "golden_records.csv", dtype=str).set_index("golden_id"),
        "pairs": pd.read_csv(out_dir / "match_pairs.csv", dtype=str),
    }


def merged_golden_ids(golden: pd.DataFrame) -> list:
    """Golden records built from 2+ source records, the interesting ones to inspect."""
    return list(golden.index[golden.n_source_records.astype(int) > 1])


def inspect_golden(golden_id: str, data: dict) -> dict:
    """Source records, pairwise matching evidence and per-field survivorship decisions for one golden record."""
    g = data["golden"].loc[golden_id]
    ids = [i.strip() for i in str(g.merged_record_ids).split("|") if i.strip()]  # outputs/golden_records.csv format
    sources = data["raw"].loc[ids].reset_index()
    pairs = data["pairs"]
    evidence = pairs[pairs.a_id.isin(ids) & pairs.b_id.isin(ids)].reset_index(drop=True)
    decisions = pd.DataFrame(
        [{"field": f, "golden_value": g[f], "won_by": g[src]} for f, src in SURVIVOR_FIELDS.items()]
    )
    return {"golden": g, "sources": sources, "evidence": evidence, "decisions": decisions, "record_ids": ids}
