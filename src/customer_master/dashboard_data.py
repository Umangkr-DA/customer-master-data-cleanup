"""Data access and calculations for the Streamlit dashboard (no streamlit import, so everything here is testable).

Every number shown in the dashboard is computed from files the pipeline wrote to outputs/ or from the raw CSVs in
data/raw/. The answer key is never read here: error examples come from outputs/case_study_evidence.json, which an
evaluation script wrote earlier.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from customer_master.common import CITIES, OUTPUT_DIR, RAW_DIR, validate_row
from customer_master.standardizers import standardize_record

FIELDS = ["full_name", "phone", "email", "city", "pincode", "signup_date"]
RULE_OF_FIELD = {"full_name": "R1", "phone": "R2", "email": "R3", "city": "R4", "pincode": "R5", "signup_date": "R6"}
SURVIVOR_COLUMN = {
    "full_name": "name_source", "phone": "phone_source", "email": "email_source", "city": "address_source",
    "pincode": "address_source", "signup_date": "date_source",
}  # fmt: skip
SURVIVORSHIP_WHY = {
    "full_name": (
        "A full first name beats an initial; then the spelling most records agree on; then the longest; "
        "tie-break CRM > Sales > Web."
    ),
    "phone": "Sales > CRM > Web (Sales confirms numbers at order time); a valid value always beats a blank.",
    "email": "Web > CRM > Sales (customers type their own e-mail on the website); a valid value beats a blank.",
    "city": "City and pincode are taken as a pair from the most complete record; tie-break CRM > Sales > Web.",
    "pincode": "City and pincode are taken as a pair from the most complete record; tie-break CRM > Sales > Web.",
    "signup_date": "Earliest valid date: the first time the customer appeared.",
}
PLACEHOLDERS = {"", "n/a", "na", "unknown", "customer", "tbd", "null", "none", "nan"}


def _read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_dashboard_data(raw_dir: Path = RAW_DIR, out_dir: Path = OUTPUT_DIR) -> dict:
    """Load every table the dashboard needs. Raises FileNotFoundError with a hint if the pipeline has not run."""
    out_dir = Path(out_dir)
    try:
        raw = pd.concat(
            [pd.read_csv(p, dtype=str, keep_default_na=False) for p in sorted(Path(raw_dir).glob("*.csv"))],
            ignore_index=True,
        )
        data = {
            "raw": raw,
            "golden": pd.read_csv(out_dir / "golden_records.csv", dtype=str, keep_default_na=False),
            "pairs": pd.read_csv(out_dir / "match_pairs.csv", dtype=str, keep_default_na=False),
            "metrics": _read_json(out_dir / "metrics.json"),
            "headline": _read_json(out_dir / "headline_metrics.json"),
            "validation": pd.read_csv(out_dir / "validation_summary.csv"),
            "matchers": pd.read_csv(out_dir / "matcher_comparison.csv"),
            "pr_curve": pd.read_csv(out_dir / "pr_curve_dev.csv"),
            "review_queue": pd.read_csv(
                out_dir / "review_queue.csv", dtype={"a_phone": str, "b_phone": str, "a_pincode": str, "b_pincode": str}
            ),
            "cluster_sizes": pd.read_csv(out_dir / "cluster_size_distribution.csv"),
            "evidence": _read_json(out_dir / "case_study_evidence.json"),
            "fs_params": _read_json(out_dir / "fs_parameters_dev.json"),
            "impact": _read_json(out_dir / "impact_estimate.json"),
        }
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{exc.filename} not found. Run `python scripts/run_pipeline.py` first.") from exc
    data["clean"] = standardize_frame(raw)
    return data


# ----------------------------------------------------------------------------- standardization and quality
def standardize_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """Apply the pipeline's own field standardizers to raw records (same functions the SQL pipeline uses)."""
    rows = [standardize_record(r) for r in raw[FIELDS].to_dict("records")]
    clean = pd.DataFrame(rows, index=raw.index, columns=FIELDS)  # columns= keeps the schema when raw is empty
    clean.insert(0, "record_id", raw.record_id.values)
    clean.insert(1, "source_system", raw.source_system.values)
    return clean


def _raw_value(v):
    """Raw cell -> value for the validation rules (empty string means missing)."""
    return None if v is None or (isinstance(v, float) and pd.isna(v)) or str(v) == "" else str(v)


def rule_flags(frame: pd.DataFrame) -> pd.DataFrame:
    """One 0/1 column per field: does the value pass its validation rule (R1..R6)? Same rules as the SQL scorecard."""
    flags = [validate_row(*(_raw_value(r[f]) for f in FIELDS)) for r in frame[FIELDS].to_dict("records")]
    out = pd.DataFrame(flags, index=frame.index)
    out.columns = [next(f for f, rid in RULE_OF_FIELD.items() if rid == c) for c in out.columns]
    return out[FIELDS]


def quality_by_source_field(raw: pd.DataFrame, clean: pd.DataFrame) -> pd.DataFrame:
    """Long table: source_system x field x stage (raw / standardized) -> pass rate, records, missing count."""
    parts = []
    for stage, frame in (("raw", raw), ("standardized", clean)):
        flags = rule_flags(frame)
        blank = frame[FIELDS].apply(
            lambda col: col.map(lambda v: _raw_value(v) is None or str(v).strip().lower() in PLACEHOLDERS)
        )
        for system, idx in frame.groupby("source_system").groups.items():
            for field in FIELDS:
                parts.append(
                    {
                        "source_system": system,
                        "field": field,
                        "stage": stage,
                        "records": len(idx),
                        "passed": int(flags.loc[idx, field].sum()),
                        "missing": int(blank.loc[idx, field].sum()),
                    }
                )
    out = pd.DataFrame(parts)
    out["pass_rate"] = out.passed / out.records
    return out


def all_rules_pass_rate(raw: pd.DataFrame, clean: pd.DataFrame) -> pd.DataFrame:
    """Share of records passing all six rules, by source system and stage."""
    rows = []
    for stage, frame in (("raw", raw), ("standardized", clean)):
        ok = rule_flags(frame).all(axis=1)
        for system, idx in frame.groupby("source_system").groups.items():
            rows.append({"source_system": system, "stage": stage, "pass_rate": float(ok.loc[idx].mean())})
    return pd.DataFrame(rows)


def _failed(flags: pd.DataFrame, present: dict, field: str) -> int:
    """Records whose value is present but fails the field's validation rule."""
    return int(((flags[field] == 0) & present[field]).sum())


def issue_counts(raw: pd.DataFrame) -> pd.DataFrame:
    """Specific problems in the raw data: invalid e-mails/phones/pincodes and inconsistent city spellings."""
    flags = rule_flags(raw)
    present = {f: raw[f].map(lambda v: _raw_value(v) is not None and str(v).strip().lower() not in PLACEHOLDERS)
               for f in FIELDS}  # fmt: skip
    city_spellings = raw.city[present["city"]].str.strip()
    city_inconsistent = int((~city_spellings.isin(CITIES)).sum())
    return pd.DataFrame(
        [
            {"issue": "Invalid e-mail (present but fails R3)", "records": _failed(flags, present, "email")},
            {"issue": "Invalid phone (present but fails R2)", "records": _failed(flags, present, "phone")},
            {"issue": "Invalid pincode (present but fails R5)", "records": _failed(flags, present, "pincode")},
            {"issue": "Inconsistent city spelling (not canonical)", "records": city_inconsistent},
        ]
    )  # fmt: skip


def city_variants(raw: pd.DataFrame, clean: pd.DataFrame, top: int = 15) -> pd.DataFrame:
    """How many different raw spellings map to each canonical city (the inconsistency the standardizer removes)."""
    df = pd.DataFrame({"raw_city": raw.city.str.strip(), "city": clean.city}).dropna()
    df = df[df.raw_city != ""]
    grouped = df.groupby("city").raw_city.agg(distinct_spellings="nunique", records="count").reset_index()
    return grouped.sort_values("distinct_spellings", ascending=False).head(top).reset_index(drop=True)


def missing_by_column(raw: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for system, grp in raw.groupby("source_system"):
        for f in FIELDS:
            miss = grp[f].map(lambda v: str(v).strip().lower() in PLACEHOLDERS).sum()
            rows.append({"source_system": system, "field": f, "missing": int(miss), "records": len(grp)})
    out = pd.DataFrame(rows)
    out["missing_pct"] = 100 * out.missing / out.records
    return out


# ----------------------------------------------------------------------------- executive dashboard
def kpis(data: dict) -> list:
    """The five headline KPIs, each with the exact source of its number."""
    m, h = data["metrics"], data["headline"]
    best_name, best = max(h["heldout"].items(), key=lambda kv: kv[1]["f1"])
    return [
        {
            "label": "Source records",
            "value": f"{m['raw_records']:,}",
            "help": "Rows in data/raw (CRM + Web + Sales).",
        },
        {
            "label": "Golden records",
            "value": f"{m['golden_records']:,}",
            "help": "One record per customer after matching and survivorship.",
        },
        {
            "label": "Duplicate records removed",
            "value": f"{m['records_merged']:,}",
            "help": f"{m['raw_records']:,} source records minus {m['golden_records']:,} golden records (easy dataset).",
        },
        {
            "label": "Data quality score",
            "value": f"{m['validation_all_rules']['golden'] * 100:.1f}%",
            "help": (
                "Share of golden records passing all 6 validation rules "
                f"(raw data: {m['validation_all_rules']['raw'] * 100:.1f}%)."
            ),
        },
        {
            "label": f"Best held-out F1 ({best_name})",
            "value": f"{best['f1'] * 100:.1f}%",
            "help": "Pair-level F1 on the hard held-out set the matchers were never tuned on.",
        },
    ]  # fmt: skip


def source_vs_golden(data: dict) -> pd.DataFrame:
    """Records per source system next to the number of golden records."""
    by_source = data["raw"].groupby("source_system").size().rename("records").reset_index()
    by_source = by_source.rename(columns={"source_system": "label"})
    by_source["kind"] = "source records"
    golden = pd.DataFrame([{"label": "Golden records", "records": len(data["golden"]), "kind": "golden records"}])
    return pd.concat([by_source, golden], ignore_index=True)


def duplicate_distribution(data: dict) -> pd.DataFrame:
    """Golden records by how many source records built them (1 = no duplicate found)."""
    sizes = data["golden"].n_source_records.astype(int).value_counts().sort_index()
    return pd.DataFrame({"source_records_per_customer": sizes.index.astype(str), "customers": sizes.values})


# ----------------------------------------------------------------------------- golden record explorer
def search_golden(data: dict, query: str, limit: int = 50) -> pd.DataFrame:
    """Case-insensitive search over golden values and over every source record's raw values."""
    q = str(query).strip().lower()
    golden = data["golden"]
    if not q:
        return golden.head(0)
    hay_golden = (
        golden[["golden_id", "full_name", "phone", "email", "merged_record_ids"]].astype(str).agg(" ".join, axis=1)
    )
    hit = set(golden.golden_id[hay_golden.str.lower().str.contains(q, regex=False)])
    raw = data["raw"]
    raw_hit = raw[raw[["record_id", "full_name", "phone", "email"]].astype(str).agg(" ".join, axis=1)
                  .str.lower().str.contains(q, regex=False)].record_id  # fmt: skip
    if len(raw_hit):
        wanted = set(raw_hit)
        for gid, ids in zip(golden.golden_id, golden.merged_record_ids):
            if wanted & {i.strip() for i in str(ids).split("|")}:
                hit.add(gid)
    return golden[golden.golden_id.isin(hit)].head(limit).reset_index(drop=True)


def explain_golden(golden_id: str, data: dict) -> dict:
    """Source records, standardized values, golden values, winning source and the rule behind each choice."""
    g = data["golden"].set_index("golden_id").loc[golden_id]
    ids = [i.strip() for i in str(g.merged_record_ids).split("|") if i.strip()]
    raw = data["raw"].set_index("record_id").loc[ids]
    clean = data["clean"].set_index("record_id").loc[ids]
    rows = []
    for f in FIELDS:
        row = {"field": f}
        for rid in ids:
            row[f"{rid} raw"] = raw.at[rid, f]
        for rid in ids:
            v = clean.at[rid, f]
            row[f"{rid} standardized"] = "" if pd.isna(v) else v
        winner = g[SURVIVOR_COLUMN[f]]
        contributing = [rid for rid in ids if clean.at[rid, "source_system"] == winner and clean.at[rid, f] == g[f]]
        row.update(
            {
                "golden_value": g[f],
                "from_source": winner,
                "from_record": contributing[0] if contributing else "",
                "why_selected": SURVIVORSHIP_WHY[f],
            }
        )
        rows.append(row)
    pairs = data["pairs"]
    evidence = pairs[pairs.a_id.isin(ids) & pairs.b_id.isin(ids)].reset_index(drop=True)
    return {
        "golden": g,
        "record_ids": ids,
        "raw": raw.reset_index(),
        "standardized": clean.reset_index(),
        "decisions": pd.DataFrame(rows),
        "evidence": evidence,
    }


# ----------------------------------------------------------------------------- matching evaluation
def matcher_table(data: dict, dataset: str) -> pd.DataFrame:
    m = data["matchers"]
    cols = ["matcher", "precision", "recall", "f1", "true_positive_pairs", "false_positive_pairs",
            "false_negative_pairs", "candidate_pairs"]  # fmt: skip
    return m[m.dataset == dataset][cols].reset_index(drop=True)


def dev_vs_heldout(data: dict) -> pd.DataFrame:
    """Per matcher and metric: development value, held-out value and the gap (held-out minus dev)."""
    m = data["matchers"]
    rows = []
    for matcher, grp in m.groupby("matcher"):
        d, h = grp[grp.dataset == "dev"].iloc[0], grp[grp.dataset == "heldout"].iloc[0]
        for metric in ("precision", "recall", "f1"):
            rows.append({"matcher": matcher, "metric": metric, "dev": d[metric], "heldout": h[metric],
                         "gap": h[metric] - d[metric]})  # fmt: skip
    return pd.DataFrame(rows)


def business_reading(precision: float, recall: float) -> list:
    """Translate precision and recall into plain business sentences (per 100 pairs, never a forecast)."""
    wrong = round((1 - precision) * 100, 1)
    missed = round((1 - recall) * 100, 1)
    return [
        f"Precision {precision * 100:.1f}%: of every 100 pairs the system merges on its own, about {wrong:g} join "
        "two different customers. Each wrong merge can send one person's mail or offers to another.",
        f"Recall {recall * 100:.1f}%: of every 100 true duplicate pairs, about {missed:g} are not merged. "
        "Those customers keep receiving duplicate messages until someone finds them.",
        "The review queue exists for exactly this gap: pairs the system is unsure about go to a person instead of "
        "being merged or ignored.",
    ]


def error_examples(data: dict) -> list:
    """False matches and missed matches (real examples from the dev set) plus a pair that belongs in human review."""
    ex = data["evidence"]["examples"]
    labels = [
        ("false_merge_namesake_household", "False match", "Two different customers merged (same name, shared phone)."),
        ("false_merge_phone_off_by_one", "False match", "Two different customers merged (phone differs by one digit)."),
        ("missed_contacts_changed", "Missed match", "Same customer missed (phone and e-mail both changed)."),
        (
            "missed_blank_name",
            "Ambiguous: sent to review",
            "Same customer, but one name is blank so it is never auto-merged.",
        ),
    ]
    out = []
    for key, kind, summary in labels:
        e = ex.get(key)
        if not e:
            continue
        recs = pd.DataFrame(e["records"]).T.reset_index().rename(columns={"index": "record_id"})
        out.append({"kind": kind, "summary": summary, "score": e["score"], "decision": e["decision"],
                    "same_customer": e["same_customer"], "records": recs})  # fmt: skip
    return out
