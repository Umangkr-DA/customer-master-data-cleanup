"""Headline numbers for the README, read from generated files only (nothing is computed from the data here).

Sources: outputs/matcher_comparison.csv, multiseed_summary.csv, review_simulation_seeds_summary.csv,
febrl_comparison.csv, scale_benchmark.csv, incremental_check.json, duckdb_verification.json, impact_estimate.json.
`build()` returns a dict, `write()` saves it to outputs/headline_metrics.json, and `readme_tokens()` lists the exact
strings the README must contain (checked at the end of scripts/run_pipeline.py).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .common import OUTPUT_DIR

HEADLINE_PATH = OUTPUT_DIR / "headline_metrics.json"
FROZEN = "fellegi-sunter"
REVIEW_K = 500  # review checkpoint quoted in the README


def _comparison(out: Path, matcher: str, dataset: str) -> dict:
    df = pd.read_csv(out / "matcher_comparison.csv")
    row = df[(df.matcher == matcher) & (df.dataset == dataset)]
    if row.empty:
        raise ValueError(f"no {matcher}/{dataset} row in matcher_comparison.csv")
    r = row.iloc[0]
    keys = ["precision", "recall", "f1", "bcubed_f1", "candidate_pairs", "runtime_s", "records"]
    return {k: float(r[k]) for k in keys}


def _multiseed(out: Path, matcher: str) -> dict:
    df = pd.read_csv(out / "multiseed_summary.csv")
    res = {}
    for metric in ("precision", "recall", "f1", "bcubed_f1"):
        r = df[(df.matcher == matcher) & (df.metric == metric)].iloc[0]
        res[metric] = {"mean": float(r["mean"]), "low": float(r.ci95_low), "high": float(r.ci95_high)}
    res["n_seeds"] = int(df[df.matcher == matcher].n_seeds.iloc[0])
    return res


def _review(out: Path, k: int) -> dict:
    df = pd.read_csv(out / "review_simulation_seeds_summary.csv")
    df["reviewed"] = df.reviewed.astype(str)  # the column also holds the label "all"
    res = {"reviewed": k}
    k = str(k)
    for metric in ("precision", "recall"):
        r = df[(df.reviewed == k) & (df.metric == metric)].iloc[0]
        res[metric] = {"mean": float(r["mean"]), "low": float(r.ci95_low), "high": float(r.ci95_high)}
    res["recall_before"] = float(df[(df.reviewed == "0") & (df.metric == "recall")].iloc[0]["mean"])
    return res


def _scale(out: Path) -> dict:
    df = pd.read_csv(out / "scale_benchmark.csv")
    res = {}
    for _, r in df.iterrows():
        n = int(r.target_records)
        ok = str(r.status) == "ok"
        res[f"{r.variant}@{n}"] = {
            "status": "ok" if ok else "failed",
            "precision": float(r.precision) if ok else None,
            "recall": float(r.recall) if ok else None,
            "f1": float(r.f1) if ok else None,
            "matching_s": float(r.matching_s) if ok else None,
            "peak_rss_mb": float(r.peak_rss_mb) if ok else None,
        }
    return res


def build(out: Path = OUTPUT_DIR) -> dict:
    out = Path(out)
    febrl = pd.read_csv(out / "febrl_comparison.csv")
    fs_febrl = febrl[febrl.matcher == FROZEN].iloc[0]
    inc = json.loads((out / "incremental_check.json").read_text(encoding="utf-8"))
    duck = json.loads((out / "duckdb_verification.json").read_text(encoding="utf-8"))
    impact = json.loads((out / "impact_estimate.json").read_text(encoding="utf-8"))["estimate"]
    return {
        "frozen_matcher": FROZEN,
        "heldout": {m: _comparison(out, m, "heldout") for m in ("exact-key", "fuzzy-rules", FROZEN)},
        "dev": {m: _comparison(out, m, "dev") for m in ("exact-key", "fuzzy-rules", FROZEN)},
        "multiseed": {m: _multiseed(out, m) for m in ("exact-key", "fuzzy-rules", FROZEN)},
        "review_simulation": _review(out, REVIEW_K),
        "febrl3": {k: float(fs_febrl[k]) for k in ("precision", "recall", "f1")},
        "scale": _scale(out),
        "incremental_equals_rebuild": bool(inc["incremental_equals_rebuild_frozen"]),
        "duckdb_all_identical": bool(duck["all_identical"]),
        "impact_net_with_review_inr": float(impact["net_saving_with_review_inr_per_year"]),
    }


def _round(obj, digits: int = 9):
    """Round floats so the JSON is byte-identical across pandas/NumPy versions (CSV parsing differs in the last ULP)."""
    if isinstance(obj, float):
        return round(obj, digits)
    if isinstance(obj, dict):
        return {k: _round(v, digits) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round(v, digits) for v in obj]
    return obj


def write(out: Path = OUTPUT_DIR) -> dict:
    h = _round(build(out))
    (Path(out) / "headline_metrics.json").write_text(json.dumps(h, indent=2) + "\n", encoding="utf-8")
    return h


def _p(x: float) -> str:
    return f"{x:.1%}"


def readme_tokens(h: dict) -> list:
    """Exact README fragments (table rows and sentences), so a number cannot match by appearing somewhere else."""
    ho, ms = h["heldout"], h["multiseed"]
    labels = {"exact-key": "exact-key (original rules)", "fuzzy-rules": "fuzzy-rules"}
    labels[h["frozen_matcher"]] = "**fellegi-sunter (recommended)**"
    toks = []
    for m, label in labels.items():
        r = ho[m]
        toks.append(
            f"| {label} | **{_p(r['precision'])}** | **{_p(r['recall'])}** | **{_p(r['f1'])}** | "
            f"{int(r['candidate_pairs']):,} |"
        )
    f = ms[h["frozen_matcher"]]
    iv = [f"**{_p(f[k]['low'])}-{_p(f[k]['high'])}**" for k in ("precision", "recall", "f1")]
    toks.append(f"precision {iv[0]}, recall {iv[1]} and F1 {iv[2]}")
    rv = h["review_simulation"]
    toks.append(
        f"recall **{_p(rv['recall_before'])}** before review, **{_p(rv['recall']['mean'])}** after "
        f"{rv['reviewed']} ranked pairs reviewed, precision **{_p(rv['precision']['mean'])}**"
    )
    fz, pr = h["scale"].get("scalable-frozen@500000"), h["scale"].get("scalable-frozen-prior@500000")
    if fz and pr and fz["status"] == "ok" and pr["status"] == "ok":
        toks.append(
            f"precision **{_p(pr['precision'])}**, recall **{_p(pr['recall'])}** with the prior-odds correction "
            f"(**{_p(fz['precision'])}** / **{_p(fz['recall'])}** without it)"
        )
        toks.append(
            f"precision {_p(pr['precision'])} at {_p(pr['recall'])} recall (F1 {_p(pr['f1'])}, against "
            f"{_p(fz['f1'])} without it)"
        )
    fb = h["febrl3"]
    toks.append(f"precision **{_p(fb['precision'])}**, recall **{_p(fb['recall'])}**, F1 **{_p(fb['f1'])}**")
    return toks
