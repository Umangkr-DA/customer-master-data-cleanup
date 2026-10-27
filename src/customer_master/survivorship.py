"""Run the survivorship SQL (sql/04_survivorship.sql) on ANY matcher's clusters, in an in-memory SQLite database.

The main pipeline builds golden records from the SQL matcher's tables. This helper lets experiments, the demo app and
incremental matching build golden records from the output of the fuzzy or Fellegi-Sunter matchers without touching
data/processed. The survivorship rules are unchanged; only the input tables are supplied from Python.
"""

from __future__ import annotations

import pandas as pd

from customer_master.clustering import cluster
from customer_master.common import CITY_PIN_PREFIX, run_sql


def build_golden(clean: pd.DataFrame, merged_pairs, edge_info: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (golden_records, cluster_map) for the standardized `clean` records and the accepted `merged_pairs`.

    edge_info optionally maps (a_id, b_id) -> (rule label, 'High'|'Medium') for the lineage columns; pairs without an
    entry are recorded as rule 'matcher' with confidence 'High'.
    """
    import sqlite3

    con = sqlite3.connect(":memory:")
    try:
        records = list(clean.record_id)
        labels = cluster(records, merged_pairs)
        clean.to_sql("clean_customers", con, index=False)
        pd.DataFrame({"record_id": list(labels), "label": list(labels.values())}).to_sql(
            "match_clusters", con, index=False
        )
        edge_info = edge_info or {}
        edges = [(a, b, *edge_info.get((a, b), ("matcher", "High"))) for a, b in merged_pairs]
        pd.DataFrame(edges, columns=["a_id", "b_id", "match_rule", "match_confidence"]).to_sql(
            "match_edges", con, index=False
        )
        con.execute("CREATE TABLE city_ref (city TEXT PRIMARY KEY, pin_prefix TEXT)")
        con.executemany("INSERT INTO city_ref VALUES (?, ?)", CITY_PIN_PREFIX.items())
        run_sql(con, "04_survivorship.sql")
        golden = pd.read_sql("SELECT * FROM golden_records ORDER BY golden_id", con)
        cmap = pd.read_sql("SELECT * FROM cluster_map", con)
    finally:
        con.close()
    return golden, cmap
