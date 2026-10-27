"""Matching tests run the real SQL files against a tiny in-memory SQLite database."""

import sqlite3


from customer_master.common import CITY_PIN_PREFIX, SQL_DIR, name_match
from customer_master.matching import build_clusters

COLS = "(record_id, source_system, full_name, phone, email, city, pincode, signup_date)"


def make_db(rows):
    con = sqlite3.connect(":memory:")
    con.create_function("name_match", 2, name_match)
    con.execute(f"CREATE TABLE clean_customers {COLS}")
    con.executemany("INSERT INTO clean_customers VALUES (?,?,?,?,?,?,?,?)", rows)
    con.execute("CREATE TABLE city_ref (city TEXT PRIMARY KEY, pin_prefix TEXT)")
    con.executemany("INSERT INTO city_ref VALUES (?,?)", CITY_PIN_PREFIX.items())
    for f in ("02_duplicate_detection.sql", "03_matching.sql"):
        con.executescript((SQL_DIR / f).read_text(encoding="utf-8"))
    return con


def rec(i, system, name, phone=None, email=None, pin=None):
    return (i, system, name, phone, email, None, pin, None)


def edges(con):
    return set(con.execute("SELECT a_id, b_id FROM match_edges").fetchall())


def test_name_match_policy():
    assert name_match("Rahul Sharma", "R Sharma") == 1
    assert name_match("Rahul Sharma", "Rohit Sharma") == 0
    assert name_match("Rahul Sharma", None) == -1


def test_identical_email_and_same_name_matches():
    con = make_db([rec("A", "CRM", "Rahul Sharma", email="r@x.com"), rec("B", "Web", "Rahul Sharma", email="r@x.com")])
    assert edges(con) == {("A", "B")}


def test_identical_phone_and_same_name_matches():
    con = make_db(
        [rec("A", "CRM", "Rahul Sharma", phone="9876543210"), rec("B", "Sales", "R Sharma", phone="9876543210")]
    )
    assert edges(con) == {("A", "B")}


def test_relatives_sharing_phone_are_not_merged():
    con = make_db(
        [rec("A", "CRM", "Rahul Sharma", phone="9876543210"), rec("B", "CRM", "Sunita Sharma", phone="9876543210")]
    )
    assert edges(con) == set()
    assert con.execute("SELECT COUNT(*) FROM rejected_pairs").fetchone()[0] == 1


def test_similar_names_without_shared_key_are_not_candidates():
    con = make_db(
        [rec("A", "CRM", "Rahul Sharma", phone="9111111112"), rec("B", "Web", "Rahul Sharma", phone="9222222223")]
    )
    assert edges(con) == set()


def test_missing_values_are_not_matching_evidence():
    con = make_db([rec("A", "CRM", "Rahul Sharma"), rec("B", "Web", "Rahul Sharma")])
    assert edges(con) == set()


def test_blank_name_needs_both_phone_and_email():
    one_key = make_db([rec("A", "CRM", None, phone="9876543210"), rec("B", "Web", "Rahul Sharma", phone="9876543210")])
    two_keys = make_db(
        [rec("A", "CRM", None, "9876543210", "r@x.com"), rec("B", "Web", "Rahul Sharma", "9876543210", "r@x.com")]
    )
    assert edges(one_key) == set() and edges(two_keys) == {("A", "B")}


def test_confidence_levels_and_rule_label():
    con = make_db(
        [
            rec("A", "CRM", "Rahul Sharma", "9876543210", "r@x.com"),
            rec("B", "Web", "Rahul Sharma", "9876543210", "r@x.com"),
            rec("C", "CRM", "Asha Rao", phone="9333333334"),
            rec("D", "Web", "Asha Rao", phone="9333333334"),
        ]
    )
    got = {(a, r, c) for a, r, c in con.execute("SELECT a_id, match_rule, match_confidence FROM match_edges")}
    assert got == {("A", "phone+email", "High"), ("C", "phone", "Medium")}


def test_name_plus_pincode_secondary_key():
    con = make_db([rec("A", "CRM", "Asha Rao", pin="462001"), rec("B", "Web", "Asha Rao", pin="462001")])
    assert edges(con) == {("A", "B")}


def test_pair_reported_once_even_if_several_keys_agree():
    con = make_db(
        [
            rec("A", "CRM", "Rahul Sharma", "9876543210", "r@x.com", "462001"),
            rec("B", "Web", "Rahul Sharma", "9876543210", "r@x.com", "462001"),
        ]
    )
    assert con.execute("SELECT COUNT(*) FROM match_edges").fetchone()[0] == 1


def test_three_records_chain_into_one_cluster():
    con = make_db(
        [
            rec("A", "CRM", "Rahul Sharma", phone="9876543210"),
            rec("B", "Web", "Rahul Sharma", "9876543210", "r@x.com"),
            rec("C", "Sales", "Rahul Sharma", email="r@x.com"),
        ]
    )
    con.execute("DROP TABLE IF EXISTS match_clusters")
    build_clusters(con)
    labels = {lab for (lab,) in con.execute("SELECT label FROM match_clusters")}
    assert labels == {"A"}


def test_empty_table_produces_no_edges():
    assert edges(make_db([])) == set()
