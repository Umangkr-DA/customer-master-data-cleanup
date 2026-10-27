import pytest

from customer_master.cluster_metrics import bcubed
from customer_master.evaluation import evaluate_clusters


def test_perfect_clustering():
    m = bcubed({"a": "1", "b": "1", "c": "2"}, {"a": "x", "b": "x", "c": "y"})
    assert (m["bcubed_precision"], m["bcubed_recall"], m["bcubed_f1"], m["purity"]) == (1.0, 1.0, 1.0, 1.0)


def test_all_singletons_has_full_precision_low_recall():
    m = bcubed({"a": "a", "b": "b", "c": "c"}, {"a": "x", "b": "x", "c": "x"})
    assert m["bcubed_precision"] == 1.0
    assert m["bcubed_recall"] == pytest.approx(1 / 3)


def test_one_big_cluster_has_full_recall_low_precision():
    m = bcubed({"a": "g", "b": "g", "c": "g", "d": "g"}, {"a": "x", "b": "x", "c": "y", "d": "z"})
    assert m["bcubed_recall"] == 1.0
    # records a,b see 2/4; c,d see 1/4 -> (0.5+0.5+0.25+0.25)/4
    assert m["bcubed_precision"] == pytest.approx(0.375)
    assert m["purity"] == pytest.approx(0.5)


def test_three_record_customer_split_weighs_less_in_bcubed_than_in_pairs():
    # customer x has 3 records, only 2 merged; customer y,z have 2 records each and are both missed
    true = {"x1": "x", "x2": "x", "x3": "x", "y1": "y", "y2": "y", "z1": "z", "z2": "z"}
    ids = list(true)
    m = evaluate_clusters(ids, true, [("x1", "x2")])
    assert m["recall"] == pytest.approx(1 / 5)  # 1 of 5 true pairs (3 + 1 + 1)
    assert m["bcubed_recall"] == pytest.approx((2 * 2 / 3 + 1 / 3 + 4 * 0.5) / 7)


def test_mismatched_keys_raise():
    with pytest.raises(ValueError):
        bcubed({"a": "1"}, {"b": "1"})
