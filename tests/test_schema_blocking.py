import pandas as pd

from customer_master.blocking import generate_candidates
from customer_master.schema import CUSTOMER, code_level, date_level, name_levels, token_level

COLS = ["record_id", "source_system", "full_name", "phone", "email", "city", "pincode", "signup_date"]


def frame(*rows):
    return pd.DataFrame([dict(zip(COLS, r)) for r in rows])


def feats_of(*rows):
    return CUSTOMER.featurize(frame(*rows))


def test_token_levels():
    assert token_level("rahul", "rahul") == 3
    assert token_level("r", "rahul") == 2 and token_level("mohd", "mohammed") == 2
    assert token_level("sandip", "sandeep") == 2  # same phonetic key
    assert token_level("sharma", "sharna") == 1  # one typo, different phonetic key
    assert token_level("rahul", "rohit") == 0
    assert token_level(None, "rahul") is None


def test_name_levels_handle_swapped_order():
    f = feats_of(
        ("A", "CRM", "Rahul Sharma", None, None, None, None, None),
        ("B", "Web", "Sharma Rahul", None, None, None, None, None),
    )
    assert name_levels(f[0], f[1]) == (3, 3)


def test_code_and_date_levels():
    assert code_level("9876543210", "9876543210") == 2
    assert code_level("9876543210", "9876543201") == 1
    assert code_level("9876543210", "6666666666") == 0 and code_level(None, "1") is None
    f = feats_of(
        ("A", "CRM", "A B", None, None, None, None, "2020-01-01"),
        ("B", "Web", "A B", None, None, None, None, "2020-06-01"),
        ("C", "Web", "A B", None, None, None, None, "2023-01-01"),
    )
    assert date_level(f[0], f[0]) == 2 and date_level(f[0], f[1]) == 1 and date_level(f[0], f[2]) == 0


def test_blocking_finds_typo_phone_and_swapped_name_pairs_but_not_all_pairs():
    rows = [
        ("A", "CRM", "Rahul Sharma", "9876543210", None, "Pune", "411001", None),
        ("B", "Web", "Rahul Sharma", "9876543201", None, None, None, None),  # phone typo (shares prefix5)
        ("C", "Web", "Sharma Rahul", None, None, "Pune", "411001", None),  # swapped name, same pincode
        ("D", "Web", "Asha Rao", "7000000001", None, "Delhi", "110001", None),
    ]
    feats = CUSTOMER.featurize(frame(*rows))
    cand, stats = generate_candidates(feats, CUSTOMER.block_rules)
    pairs = {(feats[i]["id"], feats[j]["id"]) for i, j in cand}
    assert ("A", "B") in pairs and ("A", "C") in pairs
    assert all("D" not in p for p in pairs)
    assert stats.n_pairs == len(cand) < 6 and 0 < stats.reduction_ratio < 1


def test_oversized_blocks_are_skipped_and_reported():
    rows = [(f"R{i}", "CRM", None, "9876543210", None, None, None, None) for i in range(5)]
    feats = CUSTOMER.featurize(frame(*rows))
    rule = CUSTOMER.block_rules[0].__class__("phone", CUSTOMER.block_rules[0].keys, frozenset({"phone"}), max_block=3)
    cand, stats = generate_candidates(feats, [rule])
    assert cand == {} and stats.per_rule_skipped_blocks["phone"] == 1 and stats.per_rule_skipped_records["phone"] == 5


def test_null_keys_never_form_blocks():
    feats = CUSTOMER.featurize(
        frame(("A", "CRM", None, None, None, None, None, None), ("B", "Web", None, None, None, None, None, None))
    )
    cand, _ = generate_candidates(feats, CUSTOMER.block_rules)
    assert cand == {}
