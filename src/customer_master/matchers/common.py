"""Shared plumbing for the pair-comparison matchers (candidate generation + comparison patterns)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from customer_master.blocking import generate_candidates
from customer_master.schema import Schema


def candidate_patterns(schema: Schema, clean: pd.DataFrame):
    """featurize -> block -> compare. Returns (feats, [(i, j, rule_mask, pattern)], BlockingStats)."""
    feats = schema.featurize(clean)
    cand, stats = generate_candidates(feats, schema.block_rules)
    compare = schema.compare
    rows = [(i, j, mask, compare(feats[i], feats[j])) for (i, j), mask in cand.items()]
    return feats, rows, stats


def evidence_string(schema: Schema, pattern: tuple) -> str:
    return " ".join(f"{name}={'-' if lvl is None else lvl}" for name, lvl in zip(schema.field_names, pattern))


def ordered(feats, i, j):
    a, b = feats[i]["id"], feats[j]["id"]
    return (a, b) if a < b else (b, a)


_PKG = Path(__file__).resolve().parents[1]


def code_fingerprint(*relative_files: str) -> str:
    """Hash of the source files a matcher depends on, so a code change can never reuse a stale frozen result."""
    blob = "".join((_PKG / f).read_text(encoding="utf-8") for f in relative_files)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


SHARED_CODE = ("schema.py", "blocking.py", "similarity.py", "phonetic.py", "matchers/base.py", "matchers/common.py")
