"""Survivorship tests run sql/04_survivorship.sql on a small hand-built cluster."""

import sqlite3

from customer_master.common import SQL_DIR
from customer_master.matching import build_clusters
from tests.test_matching import make_db  # noqa: F401  (shared fixture builder)


def golden_for(rows):
    con = make_db(rows)
    build_clusters(con)
    con.executescript((SQL_DIR / "04_survivorship.sql").read_text(encoding="utf-8"))
    con.row_factory = sqlite3.Row
    return con, con.execute("SELECT * FROM golden_records ORDER BY golden_id").fetchall()


ROWS = [
    ("C1", "CRM", "R Sharma", "9111111112", None, "Bhopal", None, "2021-06-01"),
    ("S1", "Sales", "Rahul Sharma", "9111111112", "r@x.com", None, "462001", "2020-01-15"),
    ("W1", "Web", "Rahul Sharma", "9111111112", "r@web.com", "Bhopal", "462002", "2022-01-01"),
]


def test_cluster_collapses_to_one_golden_record_with_lineage():
    _, g = golden_for(ROWS)
    assert len(g) == 1
    assert g[0]["n_source_records"] == 3 and g[0]["merged_record_ids"] == "C1 | S1 | W1"


def test_survivorship_rules_pick_expected_values_and_sources():
    _, (g,) = golden_for(ROWS)
    assert (g["full_name"], g["name_source"]) == ("Rahul Sharma", "Sales")  # longest, tie CRM>Sales>Web
    assert (g["email"], g["email_source"]) == ("r@web.com", "Web")  # Web priority for email
    assert (g["signup_date"], g["date_source"]) == ("2020-01-15", "Sales")  # earliest date
    assert (g["phone"], g["phone_source"]) == ("9111111112", "Sales")


def test_address_pair_comes_from_one_record_and_missing_half_is_not_invented():
    _, (g,) = golden_for(ROWS)
    # Web has city+pincode (complete pair); CRM has only city -> the complete pair wins.
    assert (g["city"], g["pincode"], g["address_source"]) == ("Bhopal", "462002", "Web")


def test_missing_value_stays_blank_when_no_record_has_it():
    _, g = golden_for(
        [
            ("A", "CRM", "Asha Rao", "9333333334", None, None, None, None),
            ("B", "Web", "Asha Rao", "9333333334", None, None, None, None),
        ]
    )
    assert g[0]["email"] is None and g[0]["city"] is None and g[0]["signup_date"] is None


def test_golden_ids_unique_and_every_record_in_exactly_one_cluster():
    con, g = golden_for(ROWS + [("X", "CRM", "Zed Kumar", "9444444445", None, None, None, None)])
    assert len({r["golden_id"] for r in g}) == len(g) == 2
    assert sum(r["n_source_records"] for r in g) == 4


def test_golden_output_is_deterministic():
    _, a = golden_for(ROWS)
    _, b = golden_for(list(reversed(ROWS)))
    assert [tuple(r) for r in a] == [tuple(r) for r in b]


def test_name_survivorship_prefers_repeated_spelling_over_one_off_typo():
    # the exact-key SQL guard would not merge a typo'd surname, so supply the cluster (as a fuzzy matcher would)
    import pandas as pd

    from customer_master.survivorship import build_golden

    cols = ["record_id", "source_system", "full_name", "phone", "email", "city", "pincode", "signup_date"]
    clean = pd.DataFrame(
        [
            ("C1", "CRM", "Pranav Mauhotra", "9222222222", None, None, None, "2020-01-01"),
            ("S1", "Sales", "Pranav Malhotra", "9222222222", None, None, None, "2020-01-02"),
            ("W1", "Web", "Pranav Malhotra", "9222222222", None, None, None, "2020-01-03"),
            ("W2", "Web", "P Malhotra", "9222222222", None, None, None, "2020-01-04"),
        ],
        columns=cols,
    )
    golden, _ = build_golden(clean, [("C1", "S1"), ("S1", "W1"), ("W1", "W2")])
    assert list(golden.full_name) == ["Pranav Malhotra"]
    assert golden.n_source_records.iloc[0] == 4


def test_name_survivorship_initial_never_beats_full_first_name_even_if_repeated():
    rows = [
        ("C1", "CRM", "R Sharma", "9333333333", None, None, None, "2020-01-01"),
        ("S1", "Sales", "R Sharma", "9333333333", None, None, None, "2020-01-02"),
        ("W1", "Web", "Rahul Sharma", "9333333333", None, None, None, "2020-01-03"),
    ]
    _, (g,) = golden_for(rows)
    assert g["full_name"] == "Rahul Sharma"
