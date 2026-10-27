import pytest

from customer_master.standardizers import (
    std_city,
    std_date,
    std_email,
    std_name,
    std_phone,
    std_pincode,
    standardize_record,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("(+91) 9562807335", "9562807335"),
        ("918478872874", "8478872874"),
        ("07057488399", "7057488399"),
        ("98765 43210", "9876543210"),
    ],
)
def test_phone_formats_are_normalized(raw, expected):
    assert std_phone(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "N/A", "1234567890", "0000000000", "9999999999", "12345", "abc"])
def test_invalid_or_missing_phone_becomes_none(raw):
    assert std_phone(raw) is None


def test_email_lowercased_trimmed_and_domain_typo_fixed():
    assert std_email("  ROHIT.K@GMAIL.CON ") == "rohit.k@gmail.com"


@pytest.mark.parametrize("raw", ["user@", "no-at-sign", "test@test.test", None, ""])
def test_invalid_email_becomes_none(raw):
    assert std_email(raw) is None


def test_name_standardization():
    assert std_name("Chatterjee, Rajesh") == "Rajesh Chatterjee"
    assert std_name("Dr. Preeti Shinde") == "Preeti Shinde"
    assert std_name("  isha   CHATTERJEE ") == "Isha Chatterjee"


@pytest.mark.parametrize("raw", ["Unknown", "Madonna", "R2D2 Smith", "", None])
def test_unusable_name_becomes_none(raw):
    assert std_name(raw) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("26 November 2018", "2018-11-26"),
        ("09/01/2023", "2023-01-09"),
        ("2024/03/13", "2024-03-13"),
    ],
)
def test_date_formats_to_iso(raw, expected):
    assert std_date(raw) == expected


@pytest.mark.parametrize("raw", ["31/02/2021", "TBD", "2010-01-01", "2099-01-01", "not a date", ""])
def test_invalid_or_out_of_range_dates_become_none(raw):
    assert std_date(raw) is None


def test_city_aliases_and_unambiguous_typo():
    assert std_city("BANGALORE") == "Bengaluru"
    assert std_city("Indor") == "Indore"
    assert std_city("Atlantis") is None


def test_pincode_cleanup():
    assert std_pincode("440-727") == "440727"
    assert std_pincode("462001.0") == "462001"
    assert std_pincode("11077A") is None
    assert std_pincode("012345") is None


def test_pincode_conflicting_with_city_is_blanked_not_guessed():
    rec = {"full_name": "A B", "phone": "", "email": "", "city": "Delhi", "pincode": "462001", "signup_date": ""}
    assert standardize_record(rec)["pincode"] is None
