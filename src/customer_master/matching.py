"""Step 3 - entity resolution: candidate pairs -> accepted matches -> clusters (sql/02 and sql/03)."""

import pandas as pd

from customer_master.common import CITY_PIN_PREFIX, OUTPUT_DIR, connect, run_sql, get_logger

log = get_logger("match")


def build_clusters(con) -> int:
    """Connected components of the match graph by min-label propagation, written in SQL.

    Every record starts as its own cluster (label = its own record_id). Each pass replaces a
    record's label by the smallest label among itself and its matched neighbours; when nothing
    changes, records with the same label belong to the same customer.
    """
    con.execute("DROP TABLE IF EXISTS match_clusters")
    con.execute("CREATE TABLE match_clusters AS SELECT record_id, record_id AS label FROM clean_customers")
    passes = 0
    while True:
        passes += 1
        con.execute("DROP TABLE IF EXISTS labels_next")
        con.execute("""
            CREATE TABLE labels_next AS
            SELECT l.record_id, MIN(l.label, COALESCE(n.min_label, l.label)) AS label
            FROM match_clusters l
            LEFT JOIN (SELECT e.src AS record_id, MIN(l2.label) AS min_label
                       FROM match_edges_sym e
                       JOIN match_clusters l2 ON l2.record_id = e.dst
                       GROUP BY e.src) n ON n.record_id = l.record_id""")
        changed = con.execute("""SELECT COUNT(*) FROM match_clusters l JOIN labels_next n USING (record_id)
                                 WHERE l.label <> n.label""").fetchone()[0]
        con.execute("DROP TABLE match_clusters")
        con.execute("ALTER TABLE labels_next RENAME TO match_clusters")
        if changed == 0:
            return passes
        if passes > 50:
            raise RuntimeError("cluster propagation did not converge")


def main() -> None:
    con = connect()
    con.execute("DROP TABLE IF EXISTS city_ref")
    con.execute("CREATE TABLE city_ref (city TEXT PRIMARY KEY, pin_prefix TEXT)")
    con.executemany("INSERT INTO city_ref VALUES (?, ?)", CITY_PIN_PREFIX.items())

    run_sql(con, "02_duplicate_detection.sql")
    run_sql(con, "03_matching.sql")
    passes = build_clusters(con)

    summary = pd.read_sql(
        """
        SELECT match_rule, match_confidence, COUNT(*) AS pairs
        FROM match_edges GROUP BY match_rule, match_confidence ORDER BY pairs DESC""",
        con,
    )
    summary.to_csv(OUTPUT_DIR / "match_rule_summary.csv", index=False)
    pd.read_sql("SELECT * FROM match_edges ORDER BY a_id, b_id", con).to_csv(
        OUTPUT_DIR / "match_pairs.csv", index=False
    )
    n_rejected = con.execute("SELECT COUNT(*) FROM rejected_pairs").fetchone()[0]
    n_clusters = con.execute("SELECT COUNT(DISTINCT label) FROM match_clusters").fetchone()[0]
    con.commit()
    con.close()
    log.info(
        "accepted %d match pairs, rejected %d by the name guard, %d clusters (converged in %d passes)",
        int(summary.pairs.sum()),
        n_rejected,
        n_clusters,
        passes,
    )


if __name__ == "__main__":
    main()
