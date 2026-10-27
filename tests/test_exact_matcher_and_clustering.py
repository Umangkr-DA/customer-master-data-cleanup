from customer_master.clustering import UnionFind, cluster
from customer_master.datasets import load_dataset
from customer_master.experiments import run_matcher
from customer_master.matchers.exact import ExactKeyMatcher


def test_union_find_components_and_deterministic_labels():
    labels = cluster(["a", "b", "c", "d", "e"], [("b", "c"), ("c", "d")])
    assert labels["b"] == labels["c"] == labels["d"] == "b" and labels["a"] == "a" and labels["e"] == "e"
    uf = UnionFind(["x", "y"])
    uf.union("y", "x")
    assert uf.find("y") == "x"


def test_exact_matcher_reproduces_the_shipped_pipeline_numbers():
    out, m = run_matcher(ExactKeyMatcher(), load_dataset("default"))
    assert (m["true_positive_pairs"], m["false_positive_pairs"], m["false_negative_pairs"]) == (699, 0, 43)
    assert m["candidate_pairs"] == 761 and len(out.merged()) == 699
