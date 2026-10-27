import pandas as pd
import pytest

from customer_master import dashboard_data as dd


@pytest.fixture(scope="module")
def data():
    return dd.load_dashboard_data()


def test_rule_pass_counts_match_the_sql_validation_scorecard(data):
    """The dashboard recomputes quality from raw data; it must agree with outputs/validation_summary.csv."""
    q = dd.quality_by_source_field(data["raw"], data["clean"])
    v = data["validation"].set_index("rule_id")
    for field, rule in dd.RULE_OF_FIELD.items():
        sub = q[q.field == field]
        assert sub[sub.stage == "raw"].passed.sum() == v.at[rule, "raw_passed"], field
        assert sub[sub.stage == "standardized"].passed.sum() == v.at[rule, "std_passed"], field


def test_kpis_come_from_pipeline_outputs(data):
    k = {x["label"]: x["value"] for x in dd.kpis(data)}
    assert k["Source records"] == f"{data['metrics']['raw_records']:,}"
    assert k["Golden records"] == f"{len(data['golden']):,}"
    assert any(label.startswith("Best held-out F1") for label in k) and len(k) == 5


def test_source_records_reconcile_with_golden_records(data):
    dist = dd.duplicate_distribution(data)
    assert (dist.customers * dist.source_records_per_customer.astype(int)).sum() == len(data["raw"])
    assert dd.source_vs_golden(data).query("kind == 'source records'").records.sum() == len(data["raw"])


def test_search_finds_by_name_record_id_and_phone_and_handles_blank_or_unknown(data):
    assert not dd.search_golden(data, "Riya Pillai").empty
    rid = data["raw"].record_id.iloc[0]
    assert not dd.search_golden(data, rid).empty
    assert not dd.search_golden(data, data["golden"].phone.iloc[0]).empty
    assert dd.search_golden(data, "").empty and dd.search_golden(data, "zzzz-no-such-customer").empty


def test_explain_golden_names_the_contributing_source_and_reason(data):
    gid = data["golden"][data["golden"].n_source_records.astype(int) > 1].golden_id.iloc[0]
    info = dd.explain_golden(gid, data)
    dec = info["decisions"].set_index("field")
    assert set(dec.index) == set(dd.FIELDS)
    assert (dec.why_selected.str.len() > 20).all()
    assert (dec.from_source.isin(["CRM", "Web", "Sales"])).all()
    assert len(info["raw"]) == len(info["record_ids"]) >= 2


def test_dev_vs_heldout_gap_is_small_for_every_matcher(data):
    gap = dd.dev_vs_heldout(data)
    assert set(gap.matcher) == {"exact-key", "fuzzy-rules", "fellegi-sunter"}
    assert gap.gap.abs().max() < 0.05  # frozen on dev, so held-out should not collapse


def test_business_reading_uses_the_given_rates():
    text = " ".join(dd.business_reading(0.965, 0.74))
    assert "3.5" in text and "26" in text and "review queue" in text


def test_error_examples_have_all_three_kinds(data):
    kinds = {e["kind"] for e in dd.error_examples(data)}
    assert {"False match", "Missed match", "Ambiguous: sent to review"} <= kinds


def test_missing_output_gives_a_helpful_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="run_pipeline"):
        dd.load_dashboard_data(out_dir=tmp_path)


def test_all_rules_pass_rate_improves_after_cleaning(data):
    r = dd.all_rules_pass_rate(data["raw"], data["clean"]).pivot(index="source_system", columns="stage",
                                                                values="pass_rate")  # fmt: skip
    assert (r.standardized > r.raw).all()


def test_issue_counts_on_a_tiny_frame():
    raw = pd.DataFrame(
        {
            "record_id": ["CRM-1", "CRM-2"], "source_system": ["CRM", "CRM"],
            "full_name": ["Rahul Sharma", "x"], "phone": ["123", ""], "email": ["not-an-email", ""],
            "city": ["BPL", "Bhopal"], "pincode": ["12", ""], "signup_date": ["2024-01-01", ""],
        }
    )  # fmt: skip
    got = dd.issue_counts(raw).set_index("issue").records
    assert got["Invalid e-mail (present but fails R3)"] == 1
    assert got["Invalid phone (present but fails R2)"] == 1
    assert got["Inconsistent city spelling (not canonical)"] == 1
