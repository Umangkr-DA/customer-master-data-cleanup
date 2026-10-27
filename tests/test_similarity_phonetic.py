import pytest

from customer_master.phonetic import indic_key
from customer_master.similarity import damerau, jaro, jaro_winkler, levenshtein, within_one_edit


def test_levenshtein_known_values():
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("", "abc") == 3 and levenshtein("same", "same") == 0


def test_damerau_counts_transposition_as_one_edit():
    assert damerau("ab", "ba") == 1 and levenshtein("ab", "ba") == 2
    assert damerau("9876543210", "9876543201") == 1


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("9876543210", "9876543210", True),
        ("9876543210", "9876543201", True),  # transposition
        ("9876543210", "9876543211", True),  # substitution
        ("987654321", "9876543210", True),  # dropped digit
        ("9876543210", "9876543012", False),
        ("12345", "1234567", False),
    ],
)
def test_within_one_edit(a, b, expected):
    assert within_one_edit(a, b) is expected


def test_jaro_winkler_reference_values():
    assert jaro_winkler("martha", "marhta") == pytest.approx(0.9611, abs=1e-3)
    assert jaro("dixon", "dicksonx") == pytest.approx(0.7667, abs=1e-3)
    assert jaro_winkler("abc", "xyz") == 0.0 and jaro_winkler("same", "same") == 1.0


def test_jaro_winkler_rewards_shared_prefix():
    assert jaro_winkler("mohammed", "mohamed") > jaro("mohammed", "mohamed")


@pytest.mark.parametrize(
    "group",
    [
        ["Mohammed", "Mohd", "Muhammad", "Mohamed", "Md"],
        ["Deepak", "Dipak"],
        ["Sandeep", "Sandip", "Sundeep"],
        ["Vikas", "Vikash"],
        ["Pooja", "Puja"],
        ["Sharma", "Sarma"],
        ["Lakshmi", "Laxmi"],
    ],
)
def test_indian_name_variants_share_a_phonetic_key(group):
    assert len({indic_key(x) for x in group}) == 1


def test_different_names_have_different_keys():
    assert indic_key("Rahul") != indic_key("Rohit")
    assert indic_key("Amit") != indic_key("Anil")
    assert indic_key("") == "" and indic_key("1234") == ""
