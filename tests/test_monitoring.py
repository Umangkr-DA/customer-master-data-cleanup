import pandas as pd
import pytest

from customer_master.monitoring import append_run, current_rates, find_drops, load_settings

RULES = ["R1", "R2", "R3", "R4", "R5", "R6"]


def _rates(v):
    return {r: v for r in RULES}


def test_first_run_has_nothing_to_compare(tmp_path):
    h = append_run(tmp_path / "h.csv", _rates(0.95), 100, "golden", "a")
    assert len(h) == 1 and find_drops(h, RULES, 2.0) == []


def test_drop_beyond_threshold_is_flagged_and_small_drop_is_not(tmp_path):
    p = tmp_path / "h.csv"
    append_run(p, _rates(0.95), 100, "golden", "a")
    r = _rates(0.95)
    r["R2"], r["R3"] = 0.90, 0.94  # -5pp and -1pp
    h = append_run(p, r, 100, "golden", "b")
    drops = find_drops(h, RULES, 2.0)
    assert [d["rule"] for d in drops] == ["R2"] and drops[0]["change_pp"] == -5.0


def test_improvement_is_not_flagged(tmp_path):
    p = tmp_path / "h.csv"
    append_run(p, _rates(0.90), 100, "golden", "a")
    assert find_drops(append_run(p, _rates(0.99), 100, "golden", "b"), RULES, 2.0) == []


def test_same_run_id_replaces_row(tmp_path):
    p = tmp_path / "h.csv"
    append_run(p, _rates(0.9), 100, "golden", "a")
    h = append_run(p, _rates(0.8), 100, "golden", "a")
    assert len(h) == 1 and h.R1.iloc[0] == 0.8


def test_current_rates_validates_input():
    v = pd.DataFrame({"rule_id": RULES, "golden_pass_rate": [0.9] * 6})
    assert current_rates(v, "golden", RULES)["R6"] == 0.9
    with pytest.raises(ValueError):
        current_rates(v, "bogus", RULES)
    with pytest.raises(ValueError):
        current_rates(v, "golden", RULES + ["R9"])


def test_negative_threshold_rejected(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"max_drop_pp": -1, "stage": "golden", "rules": []}')
    with pytest.raises(ValueError):
        load_settings(p)
