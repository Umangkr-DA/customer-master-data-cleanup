"""The answer key may only be opened by evaluation code."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "customer_master"
ALLOWED = {"validation.py", "evaluation.py", "experiments.py", "datasets.py", "heldout.py", "common.py", "febrl.py"}
# review simulation and reports evaluate against truth by design; added to ALLOWED when they exist
PATTERN = re.compile(r"answer_key|load_truth|GROUND_TRUTH_PATH|truth_path|truth_frame")


def test_only_evaluation_modules_reference_the_answer_key():
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if rel in ALLOWED or rel in {"generate_data.py", "generate_hard.py", "evaluation_sim.py"}:
            continue
        if PATTERN.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(SRC)))
    assert not offenders, f"answer key referenced outside evaluation code: {offenders}"


def test_matchers_and_standardizers_do_not_import_experiment_code():
    for path in list((SRC / "matchers").glob("*.py")) + [SRC / "standardizers.py", SRC / "clustering.py"]:
        text = path.read_text(encoding="utf-8")
        assert "customer_master.experiments" not in text and "customer_master.heldout" not in text, path.name


def test_only_the_frozen_ledger_evaluator_unlocks_the_heldout_set():
    root = SRC.parents[1]
    offenders = []
    for folder in ("src", "scripts", "app"):
        for path in (root / folder).rglob("*.py"):
            if path.name in {"heldout.py", "datasets.py"}:
                continue
            if "allow_heldout=True" in path.read_text(encoding="utf-8"):
                offenders.append(path.relative_to(root).as_posix())
    assert not offenders, f"held-out data unlocked outside heldout.py: {offenders}"


def test_review_and_dashboard_code_never_read_the_heldout_or_dev_data_folders():
    for name in ("review_store.py", "dashboard_data.py"):
        text = (SRC / name).read_text(encoding="utf-8")
        assert "data/heldout" not in text and '"heldout" /' not in text and "load_dataset" not in text, name
