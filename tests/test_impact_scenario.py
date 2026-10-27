import pytest

from customer_master.impact import SCENARIO_DEFAULTS, scenario_grid, simulate_scenario


def test_no_automation_means_no_saving():
    p = {**SCENARIO_DEFAULTS, "auto_resolve_rate": 0.0, "ambiguous_share": 0.0}
    r = simulate_scenario(**p)
    assert r["scenario_saving_inr"] == 0 and r["pipeline_cost_inr"] == r["baseline_manual_cost_inr"]


def test_hand_calculated_scenario():
    r = simulate_scenario(
        records=1000, duplicate_rate=0.1, correction_cost_inr=50, auto_resolve_rate=0.5, false_merge_rate=0.1,
        ambiguous_share=0.2, review_cost_inr=10, reviewer_accuracy=1.0, wrong_merge_cost_inr=100,
    )  # fmt: skip
    # 100 duplicates: 50 auto, 20 reviewed (all right), 30 left. wrong merges = 50 * 0.1 = 5
    assert (r["duplicates"], r["auto_resolved"], r["sent_to_review"], r["left_for_manual_fix"]) == (100, 50, 20, 30)
    assert r["baseline_manual_cost_inr"] == 5000
    assert r["pipeline_cost_inr"] == 30 * 50 + 20 * 10 + 5 * 100  # 2200
    assert r["scenario_saving_inr"] == 2800
    assert "hypothetical" in r["label"]


def test_review_share_is_capped_by_unresolved_duplicates():
    p = {**SCENARIO_DEFAULTS, "auto_resolve_rate": 0.9, "ambiguous_share": 0.9}
    r = simulate_scenario(**p)
    assert r["auto_resolved"] + r["sent_to_review"] <= r["duplicates"]


def test_expensive_wrong_merges_can_make_the_pipeline_a_loss():
    cheap = simulate_scenario(**SCENARIO_DEFAULTS)
    dear = simulate_scenario(**{**SCENARIO_DEFAULTS, "wrong_merge_cost_inr": 1_000_000})
    assert cheap["scenario_saving_inr"] > 0 > dear["scenario_saving_inr"]
    assert dear["break_even_wrong_merge_cost_inr"] < 1_000_000


@pytest.mark.parametrize("field,value", [("duplicate_rate", 1.5), ("auto_resolve_rate", -0.1), ("records", -5),
                                         ("review_cost_inr", -1)])  # fmt: skip
def test_invalid_inputs_fail_loudly(field, value):
    with pytest.raises(ValueError, match=field):
        simulate_scenario(**{**SCENARIO_DEFAULTS, field: value})


def test_grid_covers_every_combination():
    grid = scenario_grid(SCENARIO_DEFAULTS, [0.1, 0.2], [0.0, 0.1, 0.2])
    assert len(grid) == 6 and {g["duplicate_rate"] for g in grid} == {0.1, 0.2}
