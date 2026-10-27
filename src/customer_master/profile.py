"""Step 1 - load the three raw source files into SQLite and profile them (sql/01_data_profiling.sql)."""

from pathlib import Path

import pandas as pd

from customer_master.common import RAW_DIR, DB_PATH, OUTPUT_DIR, SOURCE_FILES, connect, run_sql, get_logger

log = get_logger("profile")
COLUMNS = ["record_id", "source_system", "full_name", "phone", "email", "city", "pincode", "signup_date"]


def load_raw_sources(raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    """Read crm/web/sales CSVs as text (no type guessing, no NA conversion) and stack them."""
    frames = []
    for name in SOURCE_FILES:
        path = raw_dir / f"{name}.csv"
        if not path.exists():
            raise FileNotFoundError(f"{path} not found - run `python scripts/run_pipeline.py --regenerate` first")
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        missing = set(COLUMNS) - set(frame.columns)
        if missing:
            raise ValueError(f"{path.name} is missing columns: {sorted(missing)}")
        if frame.empty:
            raise ValueError(f"{path.name} contains no records")
        frames.append(frame[COLUMNS])
    raw = pd.concat(frames, ignore_index=True)
    if raw["record_id"].duplicated().any():
        raise ValueError("record_id must be unique across source systems (duplicate source identifiers found)")
    return raw


def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()  # every run starts from a clean database
    raw = load_raw_sources()
    con = connect()
    raw.to_sql("raw_customers", con, index=False)
    log.info("loaded %d raw records from %d source systems", len(raw), raw["source_system"].nunique())

    run_sql(con, "01_data_profiling.sql")
    profile = pd.read_sql("SELECT * FROM profile_summary", con)
    sources = pd.read_sql("SELECT * FROM source_profile", con)
    OUTPUT_DIR.mkdir(exist_ok=True)
    profile.to_csv(OUTPUT_DIR / "data_profile.csv", index=False)
    sources.to_csv(OUTPUT_DIR / "source_profile.csv", index=False)
    con.commit()
    con.close()
    log.info("profile written to outputs/data_profile.csv\n%s", profile.to_string(index=False))


if __name__ == "__main__":
    main()
