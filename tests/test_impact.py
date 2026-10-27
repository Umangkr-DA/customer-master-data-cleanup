import json

import pytest

from customer_master.impact import ASSUMPTIONS_PATH, estimate, load_assumptions, sensitivity

RATES = {
    "duplicates_removed_correct": 0.15,
    "duplicates_missed": 0.05,
    "over_merged_clusters": 0.003,
    "review_extra_dups_per_pair": 0.5,
    "review_extra_overmerges_per_pair": 0.01,
}


def test_shipped_assumptions_load_and_are_non_negative():
    a = load_assumptions()
    assert a["customer_base_records"] > 0 and all(v >= 0 for v in a.values() if isinstance(v, (int, float)))


def test_estimate_scales_linearly_with_customer_base():
    a = load_assumptions()
    small = estimate(RATES, a)
    big = estimate(RATES, {**a, "customer_base_records": a["customer_base_records"] * 2})
    assert big["duplicates_removed"] == pytest.approx(2 * small["duplicates_removed"], rel=1e-3)


def test_free_wrong_merges_raise_the_net_saving():
    a = load_assumptions()
    base = estimate(RATES, a)
    free = estimate(RATES, {**a, "cost_per_wrong_merge_inr": 0})
    assert free["net_saving_without_review_inr_per_year"] > base["net_saving_without_review_inr_per_year"]
    assert free["wrong_merge_cost_inr_per_year"] == 0


def test_sensitivity_is_sorted_by_swing_and_names_assumptions():
    rows = sensitivity(RATES, load_assumptions())
    swings = [r["swing"] for r in rows]
    assert swings == sorted(swings, reverse=True) and rows[0]["assumption"]


def test_bad_assumption_is_rejected(tmp_path):
    raw = json.loads(ASSUMPTIONS_PATH.read_text(encoding="utf-8"))
    sec = next(k for k, v in raw.items() if isinstance(v, dict) and k != "_about")
    name = next(iter(raw[sec]))
    raw[sec][name]["value"] = -1
    p = tmp_path / "a.json"
    p.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        load_assumptions(p)
