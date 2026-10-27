"""End-to-end run of steps 1-6 in a temporary copy of the project (no committed file is touched).

Every module-level path constant in customer_master is re-pointed at tmp, so the real outputs/ and reports/ stay as
they are. The results must equal the committed outputs/metrics.json, which also guards against silent regressions.
"""

import importlib
import json
import shutil
import sys

import openpyxl
import pandas as pd
import pytest

from customer_master import common
from customer_master.standardize import classify

CONSTS = (
    "RAW_DIR", "GROUND_TRUTH_PATH", "PROCESSED_DIR", "SAMPLE_DIR", "OUTPUT_DIR", "REPORT_DIR", "DB_PATH", "REPORT_PATH"
)
REAL = {n: getattr(common, n) for n in CONSTS}
STEPS = ["profile", "standardize", "matching", "golden_record", "validation", "reporting"]


@pytest.fixture(scope="module")
def ran(tmp_path_factory):
    root = tmp_path_factory.mktemp("proj")
    (root / "data" / "ground_truth").mkdir(parents=True)
    shutil.copytree(REAL["RAW_DIR"], root / "data" / "raw")
    shutil.copy(REAL["GROUND_TRUTH_PATH"], root / "data" / "ground_truth" / "answer_key.csv")
    for d in ("outputs", "reports", "data/processed", "data/sample"):
        (root / d).mkdir(parents=True, exist_ok=True)
    new = {
        "RAW_DIR": root / "data" / "raw",
        "GROUND_TRUTH_PATH": root / "data" / "ground_truth" / "answer_key.csv",
        "PROCESSED_DIR": root / "data" / "processed",
        "SAMPLE_DIR": root / "data" / "sample",
        "OUTPUT_DIR": root / "outputs",
        "REPORT_DIR": root / "reports",
        "DB_PATH": root / "data" / "processed" / "customer_mdm.db",
        "REPORT_PATH": root / "reports" / "r.xlsx",
    }
    mp = pytest.MonkeyPatch()
    for name, mod in list(sys.modules.items()):
        if name.startswith("customer_master") and mod is not None:
            for const, value in new.items():
                if getattr(mod, const, None) == REAL[const]:
                    mp.setattr(mod, const, value)
    for step in STEPS:
        importlib.import_module(f"customer_master.{step}").main()
    yield root
    mp.undo()


def test_metrics_equal_committed_reference(ran):
    got = json.loads((ran / "outputs" / "metrics.json").read_text())
    ref = json.loads((REAL["OUTPUT_DIR"] / "metrics.json").read_text())
    for key in ("precision", "recall", "f1", "raw_records", "golden_records", "records_merged", "validation_all_rules"):
        assert got[key] == ref[key]


def test_real_outputs_were_not_touched(ran):
    assert not (REAL["OUTPUT_DIR"] / "r.xlsx").exists() and (ran / "outputs" / "golden_records.csv").exists()


def test_standardization_never_guesses(ran):
    clean = pd.read_csv(ran / "data" / "processed" / "clean_customers.csv", dtype=str)
    assert clean.phone.dropna().str.fullmatch(r"[6-9]\d{9}").all()
    assert clean.email.dropna().str.contains("@").all()
    log = pd.read_csv(ran / "outputs" / "cleaning_log.csv")
    assert set(log.outcome) <= {"already clean", "standardized", "unfixable -> left blank", "blank in source"}


def test_validation_summary_has_all_rules_and_stages(ran):
    v = pd.read_csv(ran / "outputs" / "validation_summary.csv")
    assert list(v.rule_id) == ["R1", "R2", "R3", "R4", "R5", "R6", "ALL"]
    assert (v.raw_pass_rate < v.std_pass_rate).all()


def test_golden_records_keep_lineage(ran):
    g = pd.read_csv(ran / "outputs" / "golden_records.csv", dtype=str)
    assert g.golden_id.is_unique
    merged = g[g.n_source_records.astype(int) > 1]
    assert merged.merged_record_ids.str.contains(r"\|").all()
    assert (g.name_source.notna() == g.full_name.notna()).all()  # lineage is set exactly when a value survives


def test_excel_report_has_expected_sheets_and_no_error_cells(ran):
    wb = openpyxl.load_workbook(ran / "reports" / "r.xlsx")
    expected = {"Data Quality", "Matching", "Cleaning Log", "By Source", "Golden Records", "Methodology"}
    assert expected <= set(wb.sheetnames)
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            assert not any(isinstance(c, str) and c.startswith("#") for c in row)


def test_evaluation_rejects_mismatched_ground_truth(ran, monkeypatch):
    from customer_master import validation

    truth = pd.read_csv(ran / "data" / "ground_truth" / "answer_key.csv").iloc[:-1]
    bad = ran / "bad_key.csv"
    truth.to_csv(bad, index=False)
    monkeypatch.setattr(validation, "GROUND_TRUTH_PATH", bad)
    con = common.connect()
    with pytest.raises(ValueError):
        validation.evaluate_matching(con)
    con.close()


@pytest.mark.parametrize(
    "raw,clean,expected",
    [
        (None, None, "blank in source"),
        ("  ", None, "blank in source"),
        ("abc", None, "unfixable -> left blank"),
        ("Rahul Sharma", "Rahul Sharma", "already clean"),
        ("rahul sharma", "Rahul Sharma", "standardized"),
    ],
)
def test_classify(raw, clean, expected):
    assert classify(raw, clean) == expected
