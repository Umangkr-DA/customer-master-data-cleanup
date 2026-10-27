import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("duckdb")

from customer_master.common import CITY_PIN_PREFIX, SQL_DIR, name_match  # noqa: E402
from customer_master.datasets import load_raw_sources, standardize_frame  # noqa: E402
from customer_master.duckdb_port import DUCKDB_SQL_DIR, run_matching_and_survivorship  # noqa: E402
from customer_master.generate_hard import generate_hard  # noqa: E402
from customer_master.matching import build_clusters  # noqa: E402


def _sqlite_reference(clean: pd.DataFrame):
    con = sqlite3.connect(":memory:")
    con.create_function("name_match", 2, name_match)
    clean.to_sql("clean_customers", con, index=False)
    con.execute("CREATE TABLE city_ref (city TEXT PRIMARY KEY, pin_prefix TEXT)")
    con.executemany("INSERT INTO city_ref VALUES (?, ?)", CITY_PIN_PREFIX.items())
    for f in ("02_duplicate_detection.sql", "03_matching.sql"):
        con.executescript((SQL_DIR / f).read_text(encoding="utf-8"))
    build_clusters(con)
    con.executescript((SQL_DIR / "04_survivorship.sql").read_text(encoding="utf-8"))
    edges = pd.read_sql("SELECT * FROM match_edges ORDER BY a_id, b_id", con)
    golden = pd.read_sql("SELECT * FROM golden_records ORDER BY golden_id", con)
    con.close()
    return edges, golden


def test_duckdb_matches_sqlite_on_hard_data():
    with tempfile.TemporaryDirectory() as tmp:
        generate_hard(Path(tmp), seed=31, n_entities=300)
        clean = standardize_frame(load_raw_sources(Path(tmp)))
    ref_edges, ref_golden = _sqlite_reference(clean)
    edges, golden = run_matching_and_survivorship(clean)
    assert len(ref_edges) > 20 and (ref_golden.n_source_records > 1).any()  # the comparison must not be trivial
    pd.testing.assert_frame_equal(edges, ref_edges, check_dtype=False)
    pd.testing.assert_frame_equal(golden, ref_golden, check_dtype=False)


def test_only_survivorship_needs_a_dialect_file():
    assert sorted(p.name for p in DUCKDB_SQL_DIR.glob("*.sql")) == ["04_survivorship.sql"]
