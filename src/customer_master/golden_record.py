"""Step 4 - survivorship: one golden record per cluster (sql/04_survivorship.sql)."""

import pandas as pd

from customer_master.common import OUTPUT_DIR, connect, run_sql, get_logger

log = get_logger("golden")


def main() -> None:
    con = connect()
    run_sql(con, "04_survivorship.sql")
    golden = pd.read_sql("SELECT * FROM golden_records ORDER BY golden_id", con)
    if golden["golden_id"].duplicated().any():
        raise ValueError("golden_id must be unique")
    n_clean = con.execute("SELECT COUNT(*) FROM clean_customers").fetchone()[0]
    if golden["n_source_records"].sum() != n_clean:
        raise ValueError("every source record must belong to exactly one golden record")
    golden.to_csv(OUTPUT_DIR / "golden_records.csv", index=False)
    con.close()
    log.info("%d source records -> %d golden records (%d merged)", n_clean, len(golden), n_clean - len(golden))


if __name__ == "__main__":
    main()
