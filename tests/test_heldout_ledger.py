import json

import pytest

import customer_master.heldout as heldout


class _Stub:
    name = "stub"

    def config(self):
        return {"matcher": "stub"}


def test_second_request_for_same_hash_uses_ledger_without_rerun(tmp_path, monkeypatch):
    calls = []

    def fake_run(matcher, ds):
        calls.append(1)
        return None, {"true_positive_pairs": 1, "false_positive_pairs": 0, "false_negative_pairs": 0,
                      "bcubed_f1": 1.0, "runtime_s": 0.1}  # fmt: skip

    monkeypatch.setattr(heldout, "run_matcher", fake_run)
    monkeypatch.setattr(heldout, "load_dataset", lambda *a, **k: None)
    path = tmp_path / "ledger.json"
    heldout.evaluate_frozen(_Stub(), path)
    heldout.evaluate_frozen(_Stub(), path)
    assert len(calls) == 1
    assert list(json.loads(path.read_text())) == [heldout.config_hash({"matcher": "stub"})]


def test_legacy_entry_is_extended_only_if_pair_metrics_reproduce(tmp_path, monkeypatch):
    h = heldout.config_hash({"matcher": "stub"})
    path = tmp_path / "ledger.json"
    old = {"true_positive_pairs": 5, "false_positive_pairs": 1, "false_negative_pairs": 2, "runtime_s": 0.2}
    path.write_text(json.dumps({h: {"config": {}, "metrics": old}}))
    monkeypatch.setattr(heldout, "load_dataset", lambda *a, **k: None)
    monkeypatch.setattr(heldout, "run_matcher", lambda m, d: (None, {**old, "bcubed_f1": 0.9}))
    assert heldout.evaluate_frozen(_Stub(), path)["bcubed_f1"] == 0.9
    path.write_text(json.dumps({h: {"config": {}, "metrics": old}}))
    monkeypatch.setattr(
        heldout, "run_matcher", lambda m, d: (None, {**old, "true_positive_pairs": 4, "bcubed_f1": 0.9})
    )
    with pytest.raises(RuntimeError):
        heldout.evaluate_frozen(_Stub(), path)
