"""Frozen-configuration discipline for the held-out set.

A matcher configuration is frozen by hashing its full config dict (rules, thresholds, parameters, code version of
SQL rules). evaluate_frozen() evaluates that hash on the held-out data AT MOST ONCE and records the result in
outputs/heldout_ledger.json together with the git commit. Asking again for the same hash returns the recorded
result without touching the data; a different config is a new hash and a new, separately recorded evaluation,
so every held-out peek is visible in the ledger.
"""

from __future__ import annotations

import hashlib
import json
import subprocess

from customer_master.common import OUTPUT_DIR, PROJECT_ROOT
from customer_master.datasets import load_dataset
from customer_master.experiments import run_matcher

LEDGER_PATH = OUTPUT_DIR / "heldout_ledger.json"


def config_hash(config: dict) -> str:
    blob = json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _git_state() -> dict:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "src", "sql"], cwd=PROJECT_ROOT, capture_output=True, text=True
            ).stdout.strip()
        )
        return {"git_commit": commit, "uncommitted_changes_in_src_or_sql": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"git_commit": None, "uncommitted_changes_in_src_or_sql": None}


def read_ledger(path=LEDGER_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def evaluate_frozen(matcher, ledger_path=LEDGER_PATH) -> dict:
    """Evaluate a matcher on the held-out set once per config hash; return the recorded metrics row."""
    cfg = matcher.config()
    h = config_hash(cfg)
    ledger = read_ledger(ledger_path)
    if h in ledger:
        entry = ledger[h]
        if "bcubed_f1" not in entry["metrics"]:
            # Metrics added after the first evaluation (B-cubed). The frozen config and data are unchanged and the
            # run is deterministic, so recompute and require the original pair metrics to be reproduced exactly.
            _, fresh = run_matcher(matcher, load_dataset("heldout", allow_heldout=True))
            for k in ("true_positive_pairs", "false_positive_pairs", "false_negative_pairs"):
                if fresh[k] != entry["metrics"][k]:
                    raise RuntimeError(f"frozen config {h} no longer reproduces its recorded held-out result ({k})")
            fresh["runtime_s"] = entry["metrics"]["runtime_s"]
            entry["metrics"] = fresh
            entry["note"] = "B-cubed/purity added by a deterministic re-run of the same frozen config (pairs identical)"
            ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return entry["metrics"]
    _, metrics = run_matcher(matcher, load_dataset("heldout", allow_heldout=True))
    ledger[h] = {"config": cfg, "metrics": metrics, **_git_state()}
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return metrics
