import json

import pytest

from customer_master import heldout
from customer_master.datasets import load_dataset, load_truth
from customer_master.matchers.exact import ExactKeyMatcher


def test_heldout_cannot_be_loaded_by_accident():
    with pytest.raises(PermissionError):
        load_dataset("heldout")


def test_default_dataset_loads_and_is_standardized():
    ds = load_dataset("default")
    assert len(ds.clean) == len(ds.raw) == 5219
    assert ds.clean.phone.dropna().str.fullmatch(r"[6-9]\d{9}").all()
    assert set(load_truth(ds).record_id) == set(ds.clean.record_id)


def test_ledger_evaluates_a_frozen_config_only_once(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.json"
    first = heldout.evaluate_frozen(ExactKeyMatcher(), ledger)
    assert len(json.loads(ledger.read_text())) == 1

    def boom(*a, **k):
        raise AssertionError("held-out data must not be evaluated twice for one frozen config")

    monkeypatch.setattr(heldout, "run_matcher", boom)
    assert heldout.evaluate_frozen(ExactKeyMatcher(), ledger) == first


def test_config_hash_changes_with_any_parameter():
    assert heldout.config_hash({"t": 1}) != heldout.config_hash({"t": 2})
    assert heldout.config_hash({"a": 1, "b": 2}) == heldout.config_hash({"b": 2, "a": 1})
