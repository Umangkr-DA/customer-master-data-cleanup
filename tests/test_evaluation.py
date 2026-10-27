import pytest

from customer_master.evaluation import pair_metrics, pairs_within


def test_pairs_within_counts_n_choose_2():
    assert len(pairs_within({"e1": ["a", "b", "c"], "e2": ["d"]})) == 3


def test_perfect_prediction():
    t = pairs_within({"e": ["a", "b"]})
    m = pair_metrics(t, set(t))
    assert (m["precision"], m["recall"], m["f1"]) == (1.0, 1.0, 1.0)


def test_false_positive_and_false_negative_counted_correctly():
    true = pairs_within({"e1": ["a", "b"], "e2": ["c", "d"]})
    pred = {("a", "b"), ("a", "c")}  # 1 TP, 1 FP, and (c,d) missed
    m = pair_metrics(true, pred)
    assert (m["true_positive_pairs"], m["false_positive_pairs"], m["false_negative_pairs"]) == (1, 1, 1)
    assert m["precision"] == pytest.approx(0.5) and m["recall"] == pytest.approx(0.5)
    assert m["f1"] == pytest.approx(0.5)


def test_three_record_customer_split_loses_pair_recall_not_precision():
    true = pairs_within({"e": ["a", "b", "c"]})  # 3 true pairs
    m = pair_metrics(true, {("a", "b")})
    assert m["recall"] == pytest.approx(1 / 3) and m["precision"] == 1.0


def test_empty_inputs_do_not_divide_by_zero():
    m = pair_metrics(set(), set())
    assert m["precision"] == 1.0 and m["recall"] == 1.0
    assert pair_metrics({("a", "b")}, set())["f1"] == 0.0
