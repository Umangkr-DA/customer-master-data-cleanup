"""Step 5 - data-quality validation (raw -> standardized -> golden) and match evaluation.

Validation : the six rules in src/customer_master/common.py applied identically at three stages (sql/05_validation.sql).
Evaluation : the matcher's clusters are compared with data/ground_truth/answer_key.csv, which is read
             ONLY here. Nothing in steps 1-4 can see the answer key, so there is no leakage.
All headline numbers are written to outputs/metrics.json - the single source of truth for the
Excel report and the README check.
"""

import json
import pandas as pd

from customer_master.cluster_metrics import bcubed
from customer_master.evaluation import pair_metrics, pairs_within, reconcile_counts
from customer_master.common import RULES, GROUND_TRUTH_PATH, OUTPUT_DIR, connect, run_sql, validate_row, get_logger

log = get_logger("validate")


# ----------------------------------------------------------------------------- validation
def write_validation_flags(con, table: str, source_table: str) -> None:
    """Create <table>(record_id, R1..R6, all_pass): one 0/1 flag per rule per record."""
    id_col = "golden_id" if source_table == "golden_records" else "record_id"
    df = pd.read_sql(
        f"SELECT {id_col} AS record_id, full_name, phone, email, city, pincode, signup_date FROM {source_table}", con
    )
    flags = [validate_row(*row[1:]) for row in df.itertuples(index=False, name=None)]
    out = pd.DataFrame(flags)
    out.insert(0, "record_id", df["record_id"])
    out["all_pass"] = out[list(RULES)].min(axis=1)
    out.to_sql(table, con, index=False, if_exists="replace")


def run_validation(con) -> pd.DataFrame:
    write_validation_flags(con, "val_raw", "raw_customers")
    write_validation_flags(con, "val_clean", "clean_customers")
    write_validation_flags(con, "val_golden", "golden_records")
    con.execute("DROP TABLE IF EXISTS rule_ref")
    con.execute("CREATE TABLE rule_ref (rule_id TEXT PRIMARY KEY, field TEXT, definition TEXT)")
    con.executemany("INSERT INTO rule_ref VALUES (?, ?, ?)", [(k, v[0], v[1]) for k, v in RULES.items()])
    run_sql(con, "05_validation.sql")
    summary = pd.read_sql("SELECT * FROM validation_summary", con)
    summary.to_csv(OUTPUT_DIR / "validation_summary.csv", index=False)
    return summary


def like_for_like(con) -> dict:
    """Validation lift on comparable denominators.

    The headline raw -> standardized -> golden series mixes denominators (5,219 records vs 4,557 golden records) and the
    raw stage is judged by FORMAT-STRICT rules, so it overstates what standardization added. Two extra views:
      merge lift   : golden pass rate vs the standardized pass rate averaged per CLUSTER (same 4,557 customers).
      format-free  : share of records whose six fields all survive deterministic standardization (nothing blank),
                     i.e. how much of the raw gap is formatting rather than missing or unrecoverable content.
    """
    per_cluster = pd.read_sql(
        "SELECT m.golden_id, AVG(v.all_pass) AS p FROM cluster_map m JOIN val_clean v USING (record_id) "
        "GROUP BY m.golden_id",
        con,
    )
    n_all = con.execute("SELECT COUNT(*) FROM clean_customers").fetchone()[0]
    present = con.execute(
        "SELECT COUNT(*) FROM clean_customers WHERE full_name IS NOT NULL AND phone IS NOT NULL AND email IS NOT NULL "
        "AND city IS NOT NULL AND pincode IS NOT NULL AND signup_date IS NOT NULL"
    ).fetchone()[0]
    golden = con.execute("SELECT AVG(all_pass) FROM val_golden").fetchone()[0]
    std_cluster = float(per_cluster.p.mean())
    return {
        "standardized_per_cluster": std_cluster,
        "golden": float(golden),
        "merge_lift_pp_like_for_like": 100 * (float(golden) - std_cluster),
        "all_fields_present_after_standardization": present / n_all,
    }


# ----------------------------------------------------------------------------- evaluation
def evaluate_matching(con) -> tuple[dict, pd.DataFrame]:
    truth = pd.read_csv(GROUND_TRUTH_PATH, dtype=str)
    predicted = pd.read_sql("SELECT record_id, golden_id FROM cluster_map", con)
    if set(truth.record_id) != set(predicted.record_id):
        raise ValueError("ground truth and pipeline cover different records")

    true_groups = truth.groupby("entity_id")["record_id"].apply(list).to_dict()
    pred_groups = predicted.groupby("golden_id")["record_id"].apply(list).to_dict()
    true_pairs, pred_pairs = pairs_within(true_groups), pairs_within(pred_groups)
    pm = pair_metrics(true_pairs, pred_pairs)

    # cluster-level view: is each true customer reproduced exactly?
    pred_sets = {frozenset(v) for v in pred_groups.values()}
    exact = sum(frozenset(v) in pred_sets for v in true_groups.values())
    multi_true = {e: v for e, v in true_groups.items() if len(v) > 1}
    exact_multi = sum(frozenset(v) in pred_sets for v in multi_true.values())
    entity_of = dict(zip(truth.record_id, truth.entity_id))
    over_merged = sum(len({entity_of[r] for r in v}) > 1 for v in pred_groups.values())

    entity_of_ = dict(zip(truth.record_id, truth.entity_id))
    pred_of = dict(zip(predicted.record_id, predicted.golden_id))
    metrics = {
        "true_customers": len(true_groups),
        "predicted_golden_records": len(pred_groups),
        "true_duplicate_pairs": len(true_pairs),
        "predicted_pairs": len(pred_pairs),
        **pm,
        **bcubed(pred_of, entity_of_),
        "customers_reproduced_exactly": exact,
        "multi_record_customers": len(multi_true),
        "multi_record_customers_reproduced_exactly": exact_multi,
        "over_merged_clusters": over_merged,
    }

    # error analysis of missed pairs, using only STANDARDIZED fields (why could the rules not see them?)
    missed = sorted(true_pairs - pred_pairs)
    clean = pd.read_sql("SELECT record_id, full_name, phone, email, city, pincode FROM clean_customers", con).set_index(
        "record_id"
    )
    rows = []
    for a, b in missed:
        ra, rb = clean.loc[a], clean.loc[b]

        def key_state(col: str) -> str:
            if pd.isna(ra[col]) or pd.isna(rb[col]):
                return "blank"
            return "same" if ra[col] == rb[col] else "different"

        phone, email = key_state("phone"), key_state("email")
        name_pin = (
            "same"
            if pd.notna(ra.full_name)
            and ra.full_name == rb.full_name
            and pd.notna(ra.pincode)
            and ra.pincode == rb.pincode
            else "not shared"
        )
        shares_key = "same" in (phone, email) or name_pin == "same"
        if not shares_key:
            reason = "no shared phone/email/name+pincode"
        elif pd.isna(ra.full_name) or pd.isna(rb.full_name):
            reason = "shared key but a name is blank (single key not accepted)"
        else:
            reason = "shared key but names conflict"
        rows.append(
            {
                "record_a": a,
                "record_b": b,
                "reason": reason,
                "phone": phone,
                "email": email,
                "name_plus_pincode": name_pin,
            }
        )
    analysis = pd.DataFrame(rows)
    analysis.to_csv(OUTPUT_DIR / "missed_pairs_analysis.csv", index=False)

    definitions = {
        "precision": "TP / (TP + FP) over record PAIRS: of the pairs the pipeline merged, how many are truly the same customer",
        "recall": "TP / (TP + FN) over record PAIRS: of the truly duplicate pairs, how many the pipeline merged",
        "true_positive_pairs": "Pairs merged by the pipeline that the answer key confirms",
        "false_positive_pairs": "Pairs merged by the pipeline that the answer key says are different customers",
        "false_negative_pairs": "True duplicate pairs the pipeline did not merge",
        "customers_reproduced_exactly": "True customers whose records ended up in one cluster containing nothing else",
        "over_merged_clusters": "Predicted clusters containing records of more than one true customer",
    }
    pd.DataFrame([{"metric": k, "value": v, "definition": definitions.get(k, "")} for k, v in metrics.items()]).to_csv(
        OUTPUT_DIR / "evaluation_summary.csv", index=False
    )
    return metrics, analysis


# ----------------------------------------------------------------------------- insights (all computed from the data)
def collect_insights(con, validation: pd.DataFrame) -> dict:
    def q(sql):
        return con.execute(sql).fetchall()

    fix = pd.read_sql("SELECT * FROM fix_log", con)
    totals = fix.groupby("field").n.sum()
    blank = fix[fix.outcome == "unfixable -> left blank"].set_index("field").n
    needs_fix = fix[fix.outcome.isin(["standardized", "unfixable -> left blank"])].groupby("field").n.sum()
    by_source = pd.read_sql(
        """
        SELECT r.source_system, COUNT(*) AS records, SUM(v.all_pass) AS raw_pass
        FROM val_raw v JOIN raw_customers r USING (record_id) GROUP BY r.source_system""",
        con,
    )
    by_source["raw_pass_rate"] = by_source.raw_pass / by_source.records
    by_source.to_csv(OUTPUT_DIR / "quality_by_source.csv", index=False)
    sizes = pd.read_sql(
        "SELECT n_source_records AS size, COUNT(*) AS golden_records FROM golden_records "
        "GROUP BY n_source_records ORDER BY size",
        con,
    )
    sizes.to_csv(OUTPUT_DIR / "cluster_size_distribution.csv", index=False)
    all_rules = validation[validation.rule_id != "ALL"]
    return {
        "unfixable_rate_by_field": {f: round(float(blank.get(f, 0)) / float(totals[f]), 4) for f in totals.index},
        "needs_fix_rate_by_field": {f: round(float(needs_fix.get(f, 0)) / float(totals[f]), 4) for f in totals.index},
        "raw_pass_rate_by_source": {r.source_system: round(r.raw_pass_rate, 4) for r in by_source.itertuples()},
        "cluster_sizes": {int(r.size): int(r.golden_records) for r in sizes.itertuples()},
        "golden_without_phone_and_email": q(
            "SELECT COUNT(*) FROM golden_records WHERE phone IS NULL AND email IS NULL"
        )[0][0],
        "golden_without_phone": q("SELECT COUNT(*) FROM golden_records WHERE phone IS NULL")[0][0],
        "golden_without_email": q("SELECT COUNT(*) FROM golden_records WHERE email IS NULL")[0][0],
        "rejected_pairs_name_conflict": q("SELECT COUNT(*) FROM rejected_pairs WHERE name_status = 0")[0][0],
        "rejected_pairs_name_blank": q("SELECT COUNT(*) FROM rejected_pairs WHERE name_status = -1")[0][0],
        "records_in_multi_system_golden": q(
            "SELECT COALESCE(SUM(n_source_records),0) FROM golden_records WHERE n_source_records > 1"
        )[0][0],
        "weakest_rule_after_standardization": all_rules.sort_values("std_pass_rate")
        .iloc[0][["rule_id", "field", "std_pass_rate"]]
        .to_dict(),
        "weakest_rule_raw": all_rules.sort_values("raw_pass_rate")
        .iloc[0][["rule_id", "field", "raw_pass_rate"]]
        .to_dict(),
        "top_cities": q(
            "SELECT city, COUNT(*) FROM golden_records WHERE city IS NOT NULL GROUP BY city ORDER BY COUNT(*) DESC, city LIMIT 3"
        ),
        "rule_counts": {
            f"{r}|{c}": n
            for r, c, n in q("SELECT match_rule, match_confidence, COUNT(*) FROM match_edges GROUP BY 1,2")
        },
    }


def main() -> None:
    con = connect()
    validation = run_validation(con)
    metrics, analysis = evaluate_matching(con)
    allrow = validation[validation.rule_id == "ALL"].iloc[0]
    metrics.update(
        {
            "raw_records": int(allrow.raw_total),
            "golden_records": int(allrow.golden_total),
            "records_merged": int(allrow.raw_total - allrow.golden_total),
            "reconciliation": reconcile_counts(
                int(allrow.raw_total),
                int(allrow.golden_total),
                metrics["true_customers"],
                metrics["over_merged_clusters"],
            ),
            "validation_all_rules": {
                "raw": float(allrow.raw_pass_rate),
                "standardized": float(allrow.std_pass_rate),
                "golden": float(allrow.golden_pass_rate),
            },
            "missed_pair_reasons": analysis.reason.value_counts().to_dict() if len(analysis) else {},
        }
    )
    metrics["validation_like_for_like"] = like_for_like(con)
    metrics["insights"] = collect_insights(con, validation)
    con.commit()
    con.close()
    (OUTPUT_DIR / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
    log.info(
        "raw=%d golden=%d merged=%d | precision=%.1f%% recall=%.1f%% f1=%.1f%% (TP=%d FP=%d FN=%d)",
        metrics["raw_records"],
        metrics["golden_records"],
        metrics["records_merged"],
        100 * metrics["precision"],
        100 * metrics["recall"],
        100 * metrics["f1"],
        metrics["true_positive_pairs"],
        metrics["false_positive_pairs"],
        metrics["false_negative_pairs"],
    )
    v = metrics["validation_all_rules"]
    log.info(
        "all-rules pass: raw %.1f%% -> standardized %.1f%% -> golden %.1f%%",
        100 * v["raw"],
        100 * v["standardized"],
        100 * v["golden"],
    )


if __name__ == "__main__":
    main()
