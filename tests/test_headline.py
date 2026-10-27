import json

import pandas as pd
import pytest

from customer_master import headline


def _write_outputs(out, review_k=500):
    rows = []
    for m, p, r in [("exact-key", 0.9, 0.4), ("fuzzy-rules", 0.95, 0.55), ("fellegi-sunter", 0.96, 0.7)]:
        for d in ("dev", "heldout"):
            rows.append(dict(matcher=m, dataset=d, precision=p, recall=r, f1=0.5, bcubed_f1=0.9,
                             candidate_pairs=10, runtime_s=1.0, records=100))
    pd.DataFrame(rows).to_csv(out / "matcher_comparison.csv", index=False)
    ms = [dict(matcher=m, metric=k, n_seeds=20, mean=0.5, std=0.1, ci95_low=0.4, ci95_high=0.6)
          for m in ("exact-key", "fuzzy-rules", "fellegi-sunter") for k in ("precision", "recall", "f1", "bcubed_f1")]
    pd.DataFrame(ms).to_csv(out / "multiseed_summary.csv", index=False)
    rv = [dict(reviewed=k, metric=m, n_seeds=20, mean=0.6 + k / 1e4, ci95_low=0.5, ci95_high=0.7)
          for k in (0, review_k) for m in ("precision", "recall")]
    pd.DataFrame(rv).to_csv(out / "review_simulation_seeds_summary.csv", index=False)
    pd.DataFrame([dict(matcher="fellegi-sunter", precision=1.0, recall=0.71, f1=0.83)]).to_csv(
        out / "febrl_comparison.csv", index=False)
    pd.DataFrame([
        dict(variant="scalable-frozen", target_records=500000, status="ok", precision=0.44, recall=0.7, f1=0.54,
             matching_s=141.0, peak_rss_mb=1950.0),
        dict(variant="scalable-frozen-prior", target_records=500000, status="ok", precision=0.92, recall=0.62,
             f1=0.74, matching_s=133.0, peak_rss_mb=1980.0),
        dict(variant="standard", target_records=500000, status="FAILED (exit 1)", precision=None, recall=None,
             matching_s=None, peak_rss_mb=None),
    ]).to_csv(out / "scale_benchmark.csv", index=False)
    (out / "incremental_check.json").write_text(json.dumps({"incremental_equals_rebuild_frozen": True}))
    (out / "duckdb_verification.json").write_text(json.dumps({"all_identical": True}))
    (out / "impact_estimate.json").write_text(
        json.dumps({"estimate": {"net_saving_with_review_inr_per_year": -5.0}}))


def test_build_reads_every_headline_from_files(tmp_path):
    _write_outputs(tmp_path)
    h = headline.build(tmp_path)
    assert h["heldout"]["fellegi-sunter"]["precision"] == 0.96
    assert h["scale"]["standard@500000"]["status"] == "failed"
    assert h["scale"]["scalable-frozen@500000"]["precision"] == 0.44
    assert h["incremental_equals_rebuild"] and h["duckdb_all_identical"]


def test_tokens_are_anchored_rows(tmp_path):
    _write_outputs(tmp_path)
    toks = headline.readme_tokens(headline.write(tmp_path))
    assert "| **fellegi-sunter (recommended)** | **96.0%** | **70.0%** | **50.0%** | 10 |" in toks
    assert any("**40.0%-60.0%**" in t for t in toks)
    expected = "precision **92.0%**, recall **62.0%** with the prior-odds correction (**44.0%** / **70.0%** without it)"
    assert expected in toks
    assert any("F1 74.0%, against 54.0% without it" in t for t in toks)
    assert (tmp_path / "headline_metrics.json").exists()


def test_missing_row_is_an_error(tmp_path):
    _write_outputs(tmp_path)
    df = pd.read_csv(tmp_path / "matcher_comparison.csv")
    df[df.dataset != "heldout"].to_csv(tmp_path / "matcher_comparison.csv", index=False)
    with pytest.raises(ValueError):
        headline.build(tmp_path)


def test_round_makes_json_stable_against_last_digit_float_noise():
    """pandas versions parse CSV floats a unit in the last place apart; the written JSON must not show it."""
    a = {"p": 0.953846153846154, "n": 3, "s": "x", "nest": {"v": [0.46937370956641433, None, True]}}
    b = {"p": 0.9538461538461539, "n": 3, "s": "x", "nest": {"v": [0.4693737095664143, None, True]}}
    assert headline._round(a) == headline._round(b)
    assert headline._round(a)["n"] == 3 and headline._round(a)["nest"]["v"][1:] == [None, True]
