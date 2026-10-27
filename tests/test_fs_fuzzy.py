import numpy as np
import pandas as pd

from customer_master.datasets import load_dataset
from customer_master.experiments import calibrate_thresholds, pr_curve, run_matcher
from customer_master.matchers.base import MERGE, NON_MATCH, REVIEW
from customer_master.matchers.fellegi_sunter import FellegiSunterMatcher
from customer_master.matchers.fuzzy import FuzzyRuleMatcher

COLS = ["record_id", "source_system", "full_name", "phone", "email", "city", "pincode", "signup_date"]


def frame(*rows):
    return pd.DataFrame([dict(zip(COLS, r)) for r in rows])


def test_fuzzy_rules_accept_typo_names_and_reject_relatives():
    df = frame(
        ("A", "CRM", "Rahul Sharma", "9876543210", "r@x.com", None, None, None),
        ("B", "Web", "Rahlu Sharma", "9876543210", None, None, None, None),  # typo, same phone
        ("C", "Web", "Sunita Sharma", "9876543210", None, None, None, None),  # relative sharing the phone
    )
    pairs = FuzzyRuleMatcher().match(df).pairs.set_index(["a_id", "b_id"])
    assert pairs.loc[("A", "B"), "decision"] == MERGE
    assert pairs.loc[("A", "C"), "decision"] == NON_MATCH


def test_fuzzy_rules_blank_name_needs_phone_and_email():
    one = frame(
        ("A", "CRM", None, "9876543210", None, None, None, None),
        ("B", "Web", "Rahul Sharma", "9876543210", None, None, None, None),
    )
    two = frame(
        ("A", "CRM", None, "9876543210", "r@x.com", None, None, None),
        ("B", "Web", "Rahul Sharma", "9876543210", "r@x.com", None, None, None),
    )
    assert FuzzyRuleMatcher().match(one).merged() == []
    assert FuzzyRuleMatcher().match(two).merged() == [("A", "B")]


def test_fs_learns_sensible_weights_on_dev_hard_data():
    ds = load_dataset("dev")
    m = FellegiSunterMatcher()
    out = m.match(ds.clean)
    table = m.weight_table()
    names = m.schema.field_names
    # agreeing exactly on phone / email / pincode must be strong positive evidence, disagreeing negative
    for field in ("phone", "email", "pincode"):
        w = table[names.index(field)]
        assert w[-1] > 5 and w[0] < 0, field
    assert np.allclose([x.sum() for x in m.m], 1) and np.allclose([x.sum() for x in m.u], 1)
    assert out.n_candidates < 0.01 * len(ds.clean) ** 2 / 2  # blocking, not all pairs
    assert set(out.pairs.decision) <= {MERGE, REVIEW, NON_MATCH}


def test_fs_conflict_veto_and_name_missing_policy():
    m = FellegiSunterMatcher(name_missing_policy="review")
    # (first, last, phone, email, city, pincode, date)
    conflict = (0, 3, 2, 2, 1, 1, 0)
    missing = (None, None, 2, 2, 1, 1, 1)
    same = (3, 3, 2, 2, 1, 1, 1)
    assert m.decide(99.0, conflict) == (REVIEW, False)
    assert m.decide(99.0, missing) == (REVIEW, False)
    assert m.decide(99.0, same) == (MERGE, True)
    assert FellegiSunterMatcher(name_missing_policy="phone+email").decide(99.0, missing) == (MERGE, True)
    assert FellegiSunterMatcher(name_missing_policy="none", conflict_veto=False).decide(99.0, conflict)[0] == MERGE


def test_matchers_are_deterministic():
    ds = load_dataset("dev")
    a = FellegiSunterMatcher(u_sample_pairs=20000).match(ds.clean).pairs
    b = FellegiSunterMatcher(u_sample_pairs=20000).match(ds.clean).pairs
    pd.testing.assert_frame_equal(a, b)


def test_calibration_respects_precision_floor_and_pr_curve_is_monotone_in_merges():
    ds = load_dataset("dev")
    out, _ = run_matcher(FellegiSunterMatcher(u_sample_pairs=20000), ds)
    cal = calibrate_thresholds(out, ds, min_precision=0.97)
    assert cal["t_review"] <= cal["t_merge"]
    curve = pr_curve(out, ds)
    chosen = curve[curve.threshold >= cal["t_merge"]].iloc[0]
    assert chosen.precision >= 0.97
    assert curve.merged_pairs.is_monotonic_decreasing
