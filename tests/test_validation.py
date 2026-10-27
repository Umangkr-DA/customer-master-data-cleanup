import pytest

from customer_master.common import RULES, validate_row
from customer_master.evaluation import reconcile_counts


def good():
    return dict(
        name="Rahul Sharma",
        phone="9876543210",
        email="rahul@gmail.com",
        city="Bhopal",
        pincode="462001",
        signup_date="2022-05-01",
    )


def test_clean_record_passes_all_rules():
    assert sum(validate_row(*good().values()).values()) == len(RULES)


@pytest.mark.parametrize(
    "field,value,rule",
    [
        ("name", "rahul sharma", "R1"),
        ("phone", "1234567890", "R2"),
        ("email", "Rahul@Gmail.com", "R3"),
        ("city", "bhopal", "R4"),
        ("pincode", "110001", "R5"),
        ("signup_date", "01/05/2022", "R6"),
        ("signup_date", "2022-02-31", "R6"),
        ("phone", None, "R2"),
        ("name", float("nan"), "R1"),
    ],
)
def test_single_defect_fails_exactly_its_rule(field, value, rule):
    rec = good()
    rec[field] = value
    flags = validate_row(*rec.values())
    assert [r for r, ok in flags.items() if not ok] == [rule]


def test_reconciliation_identity():
    r = reconcile_counts(raw_records=10, golden_records=7, true_customers=6, over_merged_clusters=0)
    assert r == {"records_merged": 3, "true_redundant_records": 4, "unmerged_redundant_records": 1, "reconciles": True}


def test_reconciliation_flags_over_merging():
    assert not reconcile_counts(10, 7, 6, over_merged_clusters=1)["reconciles"]


def test_golden_cannot_exceed_raw():
    with pytest.raises(ValueError):
        reconcile_counts(5, 6, 5, 0)
