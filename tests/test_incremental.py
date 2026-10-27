import dataclasses
import random
import tempfile
from functools import lru_cache
from pathlib import Path

import pandas as pd
import pytest

from customer_master.clustering import cluster
from customer_master.datasets import load_raw_sources, standardize_frame
from customer_master.generate_hard import generate_hard
from customer_master.incremental import IncrementalMatcher, rebuild_frozen
from customer_master.matchers.base import MERGE, REVIEW
from customer_master.matchers.fellegi_sunter import FellegiSunterMatcher
from customer_master.schema import CUSTOMER
from customer_master.survivorship import build_golden

FS_KW = dict(t_merge=4.87, t_review=-0.27, name_missing_policy="review", u_sample_pairs=20_000)


@lru_cache(maxsize=1)
def _clean() -> pd.DataFrame:
    with tempfile.TemporaryDirectory() as tmp:
        generate_hard(Path(tmp), seed=4242, n_entities=350)
        return standardize_frame(load_raw_sources(Path(tmp)))


def _split(clean, fractions, seed=1):
    ids = list(clean.record_id)
    random.Random(seed).shuffle(ids)
    start, parts = 0, []
    for fr in fractions:
        end = start + round(fr * len(ids))
        parts.append(ids[start:end])
        start = end
    parts[-1].extend(ids[start:])
    return [clean[clean.record_id.isin(p)] for p in parts]


def _run(parts, schema=CUSTOMER):
    matcher = FellegiSunterMatcher(schema, **FS_KW)
    inc = IncrementalMatcher.bootstrap(parts[0], matcher, schema)
    results = [inc.add_records(p) for p in parts[1:]]
    return inc, matcher, results


def _decided(pairs, decision):
    sub = pairs[pairs.decision == decision]
    return set(zip(sub.a_id, sub.b_id))


def test_incremental_equals_full_rebuild_with_frozen_weights():
    clean = _clean()
    inc, matcher, results = _run(_split(clean, [0.7, 0.15, 0.15]))
    assert sum(r.cap_crossed_blocks for r in results) == 0  # equality is only promised without cap crossings
    base_ids = set(_split(clean, [0.7, 0.15, 0.15])[0].record_id)
    cross = [p for r in results for p in r.merged_pairs if (p[0] in base_ids) != (p[1] in base_ids)]
    assert cross and any(r.retired_labels for r in results)  # the test must exercise new-vs-old merges
    ref = rebuild_frozen(clean, matcher)
    assert set(inc.merged_pairs) == _decided(ref, MERGE)
    assert set(zip(inc.review_pairs.a_id, inc.review_pairs.b_id)) == _decided(ref, REVIEW)
    assert inc.clusters() == cluster(list(clean.record_id), _decided(ref, MERGE))


def test_batch_order_and_size_do_not_change_the_result():
    clean = _clean()
    a, _, _ = _run(_split(clean, [0.6, 0.4]))
    b, _, _ = _run(_split(clean, [0.6, 0.1, 0.1, 0.1, 0.1], seed=1))
    # the bootstrap sets (and so the frozen weights) are identical here, only the later batching differs
    assert set(a.merged_pairs) == set(b.merged_pairs)
    assert a.clusters() == b.clusters()


def test_incremental_golden_records_equal_rebuild_for_every_cluster():
    clean = _clean()
    inc, matcher, _ = _run(_split(clean, [0.7, 0.3]))
    full_merged = list(_decided(rebuild_frozen(clean, matcher), MERGE))
    golden_full, cmap = build_golden(clean.reset_index(drop=True), full_merged)
    golden_full.insert(0, "cluster_label", golden_full.golden_id.map(cmap.groupby("golden_id").record_id.min()))
    cols = [c for c in golden_full.columns if c != "golden_id"]
    expect = golden_full[cols].sort_values("cluster_label").reset_index(drop=True)
    got = inc.golden_records()[cols].sort_values("cluster_label").reset_index(drop=True)
    pd.testing.assert_frame_equal(got, expect)


def test_result_reports_only_touched_clusters_and_retired_labels():
    clean = _clean()
    parts = _split(clean, [0.8, 0.2])
    inc, _, (res,) = _run(parts)
    labels = inc.clusters()
    assert set(res.golden.cluster_label) == res.touched_labels
    for rid in parts[1].record_id:
        assert labels[rid] in res.touched_labels
    assert not (res.retired_labels & set(labels.values()))  # retired labels are gone from the cluster map
    assert len(res.touched_labels) < len(set(labels.values()))  # far less work than the full set of clusters


def test_duplicate_record_id_is_rejected():
    clean = _clean()
    base, extra = _split(clean, [0.9, 0.1])
    inc = IncrementalMatcher.bootstrap(base, FellegiSunterMatcher(CUSTOMER, **FS_KW))
    with pytest.raises(ValueError):
        inc.add_records(pd.concat([extra, base.head(1)]))


def test_block_that_outgrows_its_cap_is_counted_and_never_loses_pairs():
    small = dataclasses.replace(
        CUSTOMER, block_rules=[dataclasses.replace(r, max_block=4) for r in CUSTOMER.block_rules]
    )
    clean = _clean()
    inc, matcher, results = _run(_split(clean, [0.5, 0.25, 0.25]), schema=small)
    assert sum(r.cap_crossed_blocks for r in results) > 0
    rebuilt = _decided(rebuild_frozen(clean, matcher, small), MERGE)
    # a rebuild drops every over-cap block; incremental already accepted pairs from when the block was small
    assert rebuilt <= set(inc.merged_pairs)
