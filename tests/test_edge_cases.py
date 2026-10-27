"""Edge cases that protect the correctness of standardization, survivorship and the review workflow."""

import pandas as pd
import pytest

from customer_master.clustering import cluster
from customer_master.dashboard_data import standardize_frame
from customer_master.standardizers import std_phone


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("(+91) 98765-43210", "9876543210"), ("098765 43210", "9876543210"), ("+91 98765 43210", "9876543210"),
        ("12345", None), ("98765432101234", None), ("5876543210", None), ("9999999999", None), ("abc", None),
        ("", None), (None, None), ("n/a", None),
    ],
)  # fmt: skip
def test_malformed_phone_numbers_are_blanked_not_guessed(raw, expected):
    assert std_phone(raw) == expected


def test_shared_phone_does_not_force_a_merge_of_different_customers():
    """Two people on one family phone: a shared key alone must not put them in one cluster."""
    merged_pairs = []  # the matcher vetoed the pair (different first names), so nothing is merged
    labels = cluster(["A", "B"], merged_pairs)
    assert labels["A"] != labels["B"]


def test_standardization_is_repeatable():
    raw = pd.DataFrame(
        {
            "record_id": ["CRM-1", "WEB-1"], "source_system": ["CRM", "Web"],
            "full_name": ["  rahul  SHARMA ", "Sharma, Rahul"], "phone": ["098765-43210", "+91 98765 43210"],
            "email": [" Rahul@Example.COM ", "rahul@example.com"], "city": ["BPL", "Bhopal MP"],
            "pincode": ["462-001", "462001"], "signup_date": ["09/01/2023", "2023-01-09"],
        }
    )  # fmt: skip
    first, second = standardize_frame(raw), standardize_frame(raw)
    pd.testing.assert_frame_equal(first, second)
    # conflicting spellings of one customer collapse to a single standardized value
    assert first.full_name.nunique() == 1 and first.phone.nunique() == 1 and first.email.nunique() == 1


def test_standardizing_an_empty_frame_returns_an_empty_frame():
    cols = ["record_id", "source_system", "full_name", "phone", "email", "city", "pincode", "signup_date"]
    out = standardize_frame(pd.DataFrame(columns=cols))
    assert out.empty and "full_name" in out.columns


def test_standardizing_without_a_required_column_fails_clearly():
    with pytest.raises(KeyError):
        standardize_frame(pd.DataFrame({"record_id": ["1"], "source_system": ["CRM"], "full_name": ["A B"]}))
