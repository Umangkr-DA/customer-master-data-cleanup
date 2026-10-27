"""Step 2 - standardize every field (src/customer_master/standardizers.py).

Unfixable values become NULL, never guesses.
"""

import pandas as pd

from customer_master.common import FIELDS, OUTPUT_DIR, PROCESSED_DIR, SAMPLE_DIR, connect, get_logger
from customer_master.standardizers import standardize_record

log = get_logger("standardize")


def classify(raw_value, clean_value) -> str:
    """What happened to one field value."""
    if raw_value is None or str(raw_value).strip() == "":
        return "blank in source"
    if clean_value is None:
        return "unfixable -> left blank"
    if str(raw_value) == clean_value:
        return "already clean"
    return "standardized"


def main() -> None:
    con = connect()
    raw = pd.read_sql("SELECT * FROM raw_customers", con)

    cleaned_rows, log_rows, examples = [], [], {}
    for rec in raw.to_dict("records"):
        clean = standardize_record(rec)
        cleaned_rows.append({"record_id": rec["record_id"], "source_system": rec["source_system"], **clean})
        for field in FIELDS:
            outcome = classify(rec[field], clean[field])
            log_rows.append((field, outcome))
            # sample for the README: skip whitespace-only fixes, which look identical on screen
            visible = outcome == "unfixable -> left blank" or str(rec[field]).strip() != clean[field]
            if visible and outcome in ("standardized", "unfixable -> left blank"):
                bucket = examples.setdefault((field, outcome), [])
                if len(bucket) < 3:  # first 3 per field/outcome: deterministic
                    bucket.append(
                        {
                            "record_id": rec["record_id"],
                            "field": field,
                            "outcome": outcome,
                            "before": rec[field],
                            "after": clean[field],
                        }
                    )

    clean_df = pd.DataFrame(cleaned_rows)
    clean_df.to_sql("clean_customers", con, index=False, if_exists="replace")
    clean_df.to_csv(PROCESSED_DIR / "clean_customers.csv", index=False)

    cleaning_log = (
        pd.DataFrame(log_rows, columns=["field", "outcome"])
        .value_counts()
        .rename("n")
        .reset_index()
        .sort_values(["field", "outcome"])
    )
    cleaning_log.to_sql("fix_log", con, index=False, if_exists="replace")
    cleaning_log.to_csv(OUTPUT_DIR / "cleaning_log.csv", index=False)

    SAMPLE_DIR.mkdir(exist_ok=True)
    pd.DataFrame([e for bucket in examples.values() for e in bucket]).to_csv(
        SAMPLE_DIR / "before_after_examples.csv", index=False
    )
    con.commit()
    con.close()
    log.info(
        "standardized %d records; %d values left blank because they could not be repaired safely",
        len(clean_df),
        int((cleaning_log.outcome == "unfixable -> left blank").mul(cleaning_log.n).sum()),
    )


if __name__ == "__main__":
    main()
