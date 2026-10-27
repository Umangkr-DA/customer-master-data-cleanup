import pandas as pd
import pytest

from customer_master.multiseed import summarize, t_crit


def test_t_crit_known_values():
    assert t_crit(19) == pytest.approx(2.093)
    assert t_crit(100) == pytest.approx(1.96)


def test_summarize_mean_and_interval():
    rows = []
    for s, v in enumerate([0.8, 0.9, 1.0, 0.9]):
        rows.append({"seed": s, "matcher": "m", "precision": v, "recall": v, "f1": v,
                     "bcubed_precision": v, "bcubed_recall": v, "bcubed_f1": v})  # fmt: skip
    out = summarize(pd.DataFrame(rows))
    p = out[(out.matcher == "m") & (out.metric == "precision")].iloc[0]
    assert p["mean"] == pytest.approx(0.9)
    # sample std of [.8,.9,1,.9] = 0.08165 ; half width = 3.182 * 0.08165 / 2
    assert p.ci95_high - p["mean"] == pytest.approx(3.182 * 0.0816497 / 2, abs=1e-4)
    assert p.n_seeds == 4


def test_single_seed_has_nan_interval():
    df = pd.DataFrame(
        [
            {
                "seed": 1,
                "matcher": "m",
                **{k: 0.5 for k in ["precision", "recall", "f1", "bcubed_precision", "bcubed_recall", "bcubed_f1"]},
            }
        ]
    )
    assert summarize(df)["ci95_low"].isna().all()
