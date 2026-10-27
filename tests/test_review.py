import math

import pandas as pd
import pytest

from customer_master.evaluation_sim import simulate_review
from customer_master.matchers.base import PAIR_COLUMNS, MatchOutput
from customer_master.review import build_review_queue, estimate_prior, parse_levels, posterior

EV_FULL = "first=3 last=3 phone=2 email=2 city=1 pincode=1 date=2"
EV_BLANK = "first=- last=- phone=2 email=2 city=1 pincode=1 date=2"


def clean_frame(ids):
    return pd.DataFrame(
        {
            "record_id": ids,
            "source_system": "CRM",
            "full_name": "Some Name",
            "phone": "9111111111",
            "email": "a@b.com",
            "city": "Delhi",
            "pincode": "110001",
            "signup_date": "2020-01-01",
        }
    )


def output(rows):
    return MatchOutput(pd.DataFrame(rows, columns=PAIR_COLUMNS), len(rows), 0.0)


def test_posterior_is_monotone_and_matches_odds_formula():
    assert posterior(0.0, 0.5) == pytest.approx(0.5)
    assert posterior(1.0, 0.5) == pytest.approx(2 / 3)  # weight 1 bit = odds 2:1
    assert posterior(5, 0.1) > posterior(2, 0.1) > posterior(-2, 0.1)
    assert math.isfinite(posterior(1e6, 0.1)) and math.isfinite(posterior(-1e6, 0.1))


def test_estimate_prior_is_between_zero_and_one_and_tracks_the_mix():
    mostly_matches = estimate_prior([10] * 90 + [-10] * 10)
    mostly_non = estimate_prior([10] * 10 + [-10] * 90)
    assert 0 < mostly_non < mostly_matches < 1
    assert estimate_prior([]) == 0.5


def test_parse_levels():
    assert parse_levels(EV_BLANK)["first"] is None
    assert parse_levels(EV_BLANK)["phone"] == 2


def test_queue_contains_review_band_and_blank_name_pairs_only():
    ids = ["A1", "A2", "B1", "B2", "C1", "C2", "D1", "D2"]
    out = output(
        [
            ("A1", "A2", 9.0, "merge", EV_FULL, True),  # auto-merged, names present -> not in queue
            ("B1", "B2", 2.0, "review", EV_FULL, True),  # review band
            ("C1", "C2", -5.0, "non-match", EV_BLANK, True),  # blank name, low score -> in queue
            ("D1", "D2", -5.0, "non-match", EV_FULL, True),  # plain non-match -> not in queue
        ]
    )
    q = build_review_queue(clean_frame(ids), out)
    assert set(zip(q.a_id, q.b_id)) == {("B1", "B2"), ("C1", "C2")}
    assert q.set_index("a_id").loc["C1", "reason_in_queue"] == "blank name"
    assert q.set_index("a_id").loc["B1", "reason_in_queue"] == "review band"
    assert "first" in q.set_index("a_id").loc["C1", "missing_fields"]


def test_pairs_already_in_the_same_cluster_are_not_queued():
    out = output(
        [
            ("A", "B", 9.0, "merge", EV_FULL, True),
            ("B", "C", 9.0, "merge", EV_FULL, True),
            ("A", "C", 2.0, "review", EV_FULL, True),  # A and C are connected through B already
        ]
    )
    assert build_review_queue(clean_frame(["A", "B", "C"]), out).empty


def test_default_ranking_orders_by_match_probability():
    ids = ["P1", "P2", "Q1", "Q2"]
    out = output([("Q1", "Q2", 3.0, "review", EV_FULL, True), ("P1", "P2", 0.5, "review", EV_FULL, True)])
    q = build_review_queue(clean_frame(ids), out)
    assert list(q.a_id) == ["Q1", "P1"]


def test_conflicting_names_get_their_own_prior_and_rank_below_agreeing_names():
    ev_conflict = "first=0 last=3 phone=2 email=2 city=1 pincode=1 date=2"
    ids = [f"{c}{i}" for c in "ABN" for i in range(1, 70)]
    # agreeing-name stratum: every pair scores high -> high prior; conflict stratum: mostly very low scores -> low prior
    rows = [(f"A{i}", f"A{i + 1}", 6.0, "review", EV_FULL, True) for i in range(1, 11)]
    rows += [("B1", "B2", 6.0, "review", ev_conflict, False)]
    rows += [(f"N{i}", f"N{i + 1}", -10.0, "non-match", ev_conflict, False) for i in range(1, 30)]
    q = build_review_queue(clean_frame(ids), output(rows)).set_index(["a_id", "b_id"])
    assert q.loc[("A1", "A2"), "match_probability"] > q.loc[("B1", "B2"), "match_probability"]


def test_unknown_ranking_rejected():
    with pytest.raises(ValueError):
        build_review_queue(clean_frame(["A", "B"]), output([("A", "B", 1.0, "review", EV_FULL, True)]), ranking="x")


def test_ranking_prefers_higher_probability_and_bigger_clusters():
    ids = ["X1", "X2", "X3", "Y1", "Z1", "Z2", "W1", "W2"]
    out = output(
        [
            ("X1", "X2", 9.0, "merge", EV_FULL, True),
            ("X2", "X3", 9.0, "merge", EV_FULL, True),
            ("X1", "Y1", 2.0, "review", EV_FULL, True),  # joins a 3-record cluster to a singleton: 3 cross pairs
            ("Z1", "Z2", 2.0, "review", EV_FULL, True),  # same score, 1 cross pair
            ("W1", "W2", 0.5, "review", EV_FULL, True),  # lower score
        ]
    )
    q = build_review_queue(clean_frame(ids), out, ranking="size_weighted_stratified")
    assert list(zip(q.a_id, q.b_id)) == [("X1", "Y1"), ("Z1", "Z2"), ("W1", "W2")]
    assert q.set_index("a_id").loc["X1", "expected_value"] == pytest.approx(
        3 * q.set_index("a_id").loc["Z1", "expected_value"], rel=1e-3
    )
    assert list(q["rank"]) == [1, 2, 3]
    assert q.expected_value.is_monotonic_decreasing


def test_simulation_endpoints_and_monotone_recall_for_perfect_reviewer():
    ids = ["A1", "A2", "B1", "B2", "C1", "C2"]
    entity = {"A1": "a", "A2": "a", "B1": "b", "B2": "b", "C1": "c", "C2": "d"}  # C pair is NOT a match
    out = output(
        [
            ("A1", "A2", 9.0, "merge", EV_FULL, True),
            ("B1", "B2", 2.0, "review", EV_FULL, True),
            ("C1", "C2", 1.5, "review", EV_FULL, True),
        ]
    )
    q = build_review_queue(clean_frame(ids), out)
    sim = simulate_review(ids, entity, out, q, step=1)
    assert list(sim.reviewed) == [0, 1, 2]
    assert sim.recall.iloc[0] == pytest.approx(0.5)  # 1 of 2 true pairs before any review
    assert sim.recall.iloc[-1] == 1.0 and sim.precision.iloc[-1] == 1.0  # reviewer rejected the false pair
    assert sim.recall.is_monotonic_increasing


def test_simulation_is_deterministic_and_random_order_reaches_same_endpoint_for_perfect_reviewer():
    ids = ["A1", "A2", "B1", "B2"]
    entity = {"A1": "a", "A2": "a", "B1": "b", "B2": "b"}
    out = output([("A1", "A2", 2.0, "review", EV_FULL, True), ("B1", "B2", 1.0, "review", EV_FULL, True)])
    q = build_review_queue(clean_frame(ids), out)
    a = simulate_review(ids, entity, out, q, order="random", seed=3, step=1)
    b = simulate_review(ids, entity, out, q, order="random", seed=3, step=1)
    pd.testing.assert_frame_equal(a, b)
    assert a.recall.iloc[-1] == simulate_review(ids, entity, out, q, step=1).recall.iloc[-1] == 1.0


def test_imperfect_reviewer_can_add_a_false_merge():
    ids = ["C1", "C2"]
    entity = {"C1": "c", "C2": "d"}
    out = output([("C1", "C2", 1.5, "review", EV_FULL, True)])
    q = build_review_queue(clean_frame(ids), out)
    sim = simulate_review(ids, entity, out, q, accuracy=0.0, step=1)  # always wrong
    assert sim.precision.iloc[-1] == 0.0


def test_conflicts_last_lists_every_conflicting_name_pair_after_the_others():
    ev_conflict = "first=0 last=3 phone=2 email=2 city=1 pincode=1 date=2"
    ids = ["A1", "A2", "B1", "B2"]
    out = output([("A1", "A2", 20.0, "review", ev_conflict, False), ("B1", "B2", 0.0, "review", EV_FULL, True)])
    q = build_review_queue(clean_frame(ids), out, ranking="conflicts_last")
    assert list(q.a_id) == ["B1", "A1"]
