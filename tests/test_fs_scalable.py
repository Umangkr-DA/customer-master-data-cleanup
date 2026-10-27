import tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import customer_master.schema as schema_module
from customer_master.datasets import load_raw_sources, standardize_frame
from customer_master.generate_hard import generate_hard
from customer_master.matchers.base import NON_MATCH
from customer_master.matchers.fellegi_sunter import FellegiSunterMatcher
from customer_master.matchers.fs_scalable import ScalableFellegiSunter, cached_token_levels

KW = dict(t_merge=4.87, t_review=-0.27, name_missing_policy="review", u_sample_pairs=20_000)


@lru_cache(maxsize=2)
def _clean(seed: int) -> pd.DataFrame:
    with tempfile.TemporaryDirectory() as tmp:
        generate_hard(Path(tmp), seed=seed, n_entities=400)
        return standardize_frame(load_raw_sources(Path(tmp)))


@pytest.mark.parametrize("seed", [11, 12])
def test_scalable_matches_reference_decisions_and_scores(seed):
    clean = _clean(seed)
    ref = FellegiSunterMatcher(**KW).match(clean)
    fast = ScalableFellegiSunter(**KW).match(clean)
    assert fast.n_candidates == ref.n_candidates
    kept = ref.pairs[ref.pairs.decision != NON_MATCH].sort_values(["a_id", "b_id"]).reset_index(drop=True)
    assert len(fast.pairs) == len(kept) > 0
    assert list(zip(fast.pairs.a_id, fast.pairs.b_id)) == list(zip(kept.a_id, kept.b_id))
    assert list(fast.pairs.decision) == list(kept.decision)
    assert list(fast.pairs.guard_ok) == list(kept.guard_ok)
    assert list(fast.pairs.evidence) == list(kept.evidence)
    # EM sums patterns in a different order, so scores may differ at floating-point noise level only
    np.testing.assert_allclose(fast.pairs.score, kept.score, atol=1e-9)
    assert set(fast.merged()) == set(ref.merged())


def test_scalable_reports_same_blocking_statistics():
    clean = _clean(11)
    ref = FellegiSunterMatcher(**KW).match(clean)
    fast = ScalableFellegiSunter(**KW).match(clean)
    assert fast.info["blocking"]["per_rule_pairs"] == ref.info["blocking"]["per_rule_pairs"]
    assert fast.info["blocking"]["skipped_blocks"] == ref.info["blocking"]["skipped_blocks"]
    assert fast.info["distinct_patterns"] < fast.n_candidates  # the point of per-pattern scoring


def test_token_cache_is_restored_and_does_not_change_results():
    original = schema_module.token_level
    with cached_token_levels():
        assert schema_module.token_level is not original
        assert schema_module.token_level("rahul", "rahu") == original("rahul", "rahu")
        assert schema_module.token_level(None, "x") is None
    assert schema_module.token_level is original


def test_split_em_mode_is_rejected():
    with pytest.raises(ValueError):
        ScalableFellegiSunter(em_mode="split")


def test_config_names_the_variant():
    cfg = ScalableFellegiSunter(**KW).config()
    assert cfg["matcher"] == "fellegi-sunter-scalable" and "code_sha_scalable" in cfg


def test_frozen_weights_are_reused_and_not_refitted():
    a, b = _clean(11), _clean(12)
    m = ScalableFellegiSunter(**KW).fit_on(a)
    m_before = [x.copy() for x in m.m]
    u_before = [x.copy() for x in m.u]
    out_b = m.match(b)
    assert all(np.array_equal(x, y) for x, y in zip(m.m, m_before))  # no re-fit on b
    assert all(np.array_equal(x, y) for x, y in zip(m.u, u_before))
    # frozen on its own fit data gives the same decisions as the normal fit-and-score
    plain = ScalableFellegiSunter(**KW).match(a)
    again = m.match(a)
    assert list(plain.pairs.decision) == list(again.pairs.decision)
    assert out_b.n_candidates > 0 and m.config()["freeze_weights"] is True


def test_freeze_without_fit_is_an_error():
    m = ScalableFellegiSunter(**KW)
    m.freeze_weights = True
    with pytest.raises(ValueError):
        m.match(_clean(11))


def test_prior_correction_raises_thresholds_only_for_larger_files():
    a, b = _clean(11), _clean(12)
    m = ScalableFellegiSunter(prior_correction=True, **KW).fit_on(a)
    assert m.threshold_shift(len(a)) == 0.0  # same size as the fit extract: no change
    assert m.threshold_shift(len(a) // 2) == 0.0  # never lowered for smaller files
    assert m.threshold_shift(len(a) * 8) == pytest.approx(3.0)  # log2(8)
    t = (m.t_merge, m.t_review)
    m.match(b)
    assert (m.t_merge, m.t_review) == t  # thresholds restored after every call


def test_prior_correction_never_adds_merges_and_is_a_noop_when_off():
    a = _clean(11)
    base = ScalableFellegiSunter(**KW).fit_on(a)
    corr = ScalableFellegiSunter(prior_correction=True, **KW).fit_on(a)
    big = _clean(12)
    corr.n_reference = max(1, len(big) // 4)  # pretend the weights were fitted on a smaller extract
    m0, m1 = set(base.match(big).merged()), set(corr.match(big).merged())
    assert m1 <= m0
    assert corr.config()["prior_correction"] is True and base.config()["prior_correction"] is False


def test_prior_correction_without_frozen_weights_is_an_error():
    m = ScalableFellegiSunter(prior_correction=True, **KW)
    with pytest.raises(ValueError):
        m.threshold_shift(1000)
