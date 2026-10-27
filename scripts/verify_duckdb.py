"""Verify that the DuckDB port reproduces the SQLite pipeline's tables exactly.

    python scripts/run_pipeline.py          # produces data/processed/customer_mdm.db (the SQLite reference)
    python scripts/verify_duckdb.py         # needs `pip install duckdb`

Runs the five SQL stages on DuckDB, starting from the same input tables the SQLite run used (raw_customers,
clean_customers, val_raw / val_clean / val_golden, rule_ref), and compares every output table cell by cell.
Writes outputs/duckdb_verification.json. Exit status is non-zero if any table differs.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from customer_master import duckdb_port as dp  # noqa: E402
from customer_master.common import DB_PATH, OUTPUT_DIR  # noqa: E402

INPUTS = ["raw_customers", "clean_customers", "val_raw", "val_clean", "val_golden", "rule_ref"]
OUTPUTS = {  # table -> ORDER BY used for both engines
    "profile_summary": "field",
    "source_profile": "source_system",
    "match_edges": "a_id, b_id",
    "match_clusters": "record_id",
    "golden_records": "golden_id",
    "validation_summary": "CASE WHEN rule_id = 'ALL' THEN 1 ELSE 0 END, rule_id",
}


def same(a: pd.DataFrame, b: pd.DataFrame) -> tuple:
    if list(a.columns) != list(b.columns):
        return False, f"columns differ: {list(a.columns)} vs {list(b.columns)}"
    if len(a) != len(b):
        return False, f"row counts differ: {len(a)} vs {len(b)}"
    a, b = a.reset_index(drop=True).astype(object), b.reset_index(drop=True).astype(object)
    a, b = a.where(a.notna(), None), b.where(b.notna(), None)
    bad = 0
    for col in a.columns:
        for x, y in zip(a[col], b[col]):
            if x is None and y is None:
                continue
            if x is None or y is None:
                bad += 1
            elif isinstance(x, (int, float)) and isinstance(y, (int, float)):
                bad += abs(float(x) - float(y)) > 1e-9
            else:
                bad += str(x) != str(y)
    return bad == 0, f"{bad} differing cells"


def main() -> None:
    if not DB_PATH.exists():
        sys.exit("run scripts/run_pipeline.py first: the SQLite reference database is missing")
    lite = sqlite3.connect(DB_PATH)
    con = dp.connect()
    for t in INPUTS:
        dp.load_table(con, t, pd.read_sql(f"SELECT * FROM {t}", lite))
    dp.load_city_ref(con)

    t0 = time.perf_counter()
    dp.run_sql(con, "01_data_profiling.sql")
    dp.run_sql(con, "02_duplicate_detection.sql")
    dp.run_sql(con, "03_matching.sql")
    passes = dp.build_clusters(con)
    dp.run_sql(con, "04_survivorship.sql")
    dp.run_sql(con, "05_validation.sql")
    duck_s = time.perf_counter() - t0

    report, ok = {}, True
    for table, order in OUTPUTS.items():
        ref = pd.read_sql(f"SELECT * FROM {table} ORDER BY {order}", lite)
        got = con.execute(f"SELECT * FROM {table} ORDER BY {order}").df()
        equal, detail = same(ref, got)
        ok &= equal
        report[table] = {"rows": len(ref), "identical": bool(equal), "detail": detail}
        print(f"{table:20s} rows={len(ref):5d} identical={equal} ({detail})")
    out = {
        "duckdb_version": dp._duckdb().__version__,
        "cluster_passes": passes,
        "duckdb_sql_seconds": round(duck_s, 2),
        "all_identical": bool(ok),
        "tables": report,
    }
    (OUTPUT_DIR / "duckdb_verification.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
