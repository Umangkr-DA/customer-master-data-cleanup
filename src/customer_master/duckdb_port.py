"""DuckDB port of the SQL pipeline (optional; the core pipeline stays on SQLite and does not import this module).

Result of the port, stage by stage (verified by scripts/verify_duckdb.py and tests/test_duckdb_port.py):

  01_data_profiling.sql      runs unchanged
  02_duplicate_detection.sql runs unchanged
  03_matching.sql            runs unchanged (the name_match() Python function is registered as a DuckDB UDF)
  cluster propagation        SQLite's two-argument MIN(a, b) becomes LEAST(a, b)   (matching.build_clusters)
  04_survivorship.sql        needs a dialect file, sql/duckdb/04_survivorship.sql: GROUP_CONCAT -> STRING_AGG with an
                             explicit ORDER BY, and one GROUP BY that SQLite tolerated but DuckDB rejects
  05_validation.sql          runs unchanged

DuckDB is an in-process engine, so the Python side (name_match UDF, input tables as DataFrames) is the same shape as
the SQLite path. Nothing is claimed beyond what the verification script compares: identical profile, match edges,
clusters, golden records and validation scorecard on the project's data.
"""

from __future__ import annotations

import pandas as pd

from customer_master.common import CITY_PIN_PREFIX, PROJECT_ROOT, SQL_DIR, name_match

DUCKDB_SQL_DIR = PROJECT_ROOT / "sql" / "duckdb"


def _duckdb():
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - exercised only without the optional dependency
        raise ImportError("the DuckDB port needs `pip install duckdb` (optional, see requirements-dev.txt)") from exc
    return duckdb


def connect():
    """In-memory DuckDB connection with name_match() registered (NULL arguments are passed through to Python)."""
    con = _duckdb().connect()
    con.create_function("name_match", name_match, ["VARCHAR", "VARCHAR"], "INTEGER", null_handling="special")
    return con


def load_table(con, name: str, frame: pd.DataFrame) -> None:
    con.register("_tmp_frame", frame)
    con.execute(f"DROP TABLE IF EXISTS {name}")
    con.execute(f"CREATE TABLE {name} AS SELECT * FROM _tmp_frame")
    con.unregister("_tmp_frame")


def load_city_ref(con) -> None:
    con.execute("DROP TABLE IF EXISTS city_ref")
    con.execute("CREATE TABLE city_ref (city VARCHAR PRIMARY KEY, pin_prefix VARCHAR)")
    con.executemany("INSERT INTO city_ref VALUES (?, ?)", list(CITY_PIN_PREFIX.items()))


def run_sql(con, filename: str) -> None:
    """Run sql/duckdb/<file> when a dialect file exists, else the shared sql/<file> unchanged."""
    path = DUCKDB_SQL_DIR / filename
    if not path.exists():
        path = SQL_DIR / filename
    con.execute(path.read_text(encoding="utf-8"))


def build_clusters(con) -> int:
    """Min-label propagation (connected components of match_edges_sym). Same algorithm as matching.build_clusters."""
    con.execute("DROP TABLE IF EXISTS match_clusters")
    con.execute("CREATE TABLE match_clusters AS SELECT record_id, record_id AS label FROM clean_customers")
    passes = 0
    while True:
        passes += 1
        con.execute("DROP TABLE IF EXISTS labels_next")
        con.execute("""
            CREATE TABLE labels_next AS
            SELECT l.record_id, LEAST(l.label, COALESCE(n.min_label, l.label)) AS label
            FROM match_clusters l
            LEFT JOIN (SELECT e.src AS record_id, MIN(l2.label) AS min_label
                       FROM match_edges_sym e
                       JOIN match_clusters l2 ON l2.record_id = e.dst
                       GROUP BY e.src) n ON n.record_id = l.record_id""")
        changed = con.execute(
            "SELECT COUNT(*) FROM match_clusters l JOIN labels_next n USING (record_id) WHERE l.label <> n.label"
        ).fetchone()[0]
        con.execute("DROP TABLE match_clusters")
        con.execute("ALTER TABLE labels_next RENAME TO match_clusters")
        if changed == 0:
            return passes
        if passes > 50:
            raise RuntimeError("cluster propagation did not converge")


def run_matching_and_survivorship(clean: pd.DataFrame):
    """Standardized records -> (match_edges, golden_records) using the exact-key SQL on DuckDB."""
    con = connect()
    try:
        load_table(con, "clean_customers", clean)
        load_city_ref(con)
        run_sql(con, "02_duplicate_detection.sql")
        run_sql(con, "03_matching.sql")
        build_clusters(con)
        run_sql(con, "04_survivorship.sql")
        edges = con.execute("SELECT * FROM match_edges ORDER BY a_id, b_id").df()
        golden = con.execute("SELECT * FROM golden_records ORDER BY golden_id").df()
        return edges, golden
    finally:
        con.close()
