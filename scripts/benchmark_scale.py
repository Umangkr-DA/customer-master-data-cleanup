"""Phase 5: runtime / memory / quality of the frozen Fellegi-Sunter matcher at 5k, 50k and 500k records.

    python scripts/benchmark_scale.py                      # all sizes, both variants, one subprocess per run
    python scripts/benchmark_scale.py --sizes 5000 50000   # subset
    python scripts/benchmark_scale.py --variants scalable  # subset of {standard, scalable}
    python scripts/benchmark_scale.py --worker 50000 standard   # (internal) one run, prints one JSON line

Variants: "standard" = the shipped FellegiSunterMatcher (replicated stage by stage for timings); "scalable" =
ScalableFellegiSunter (same decisions, tested equal; see matchers/fs_scalable.py); "scalable-frozen" = the same, with
m/u fitted once on an independent 50k-record extract (different seed) and frozen, because EM on the full candidate set
becomes unstable as the file grows (see the 500k rows); "scalable-frozen-prior" = the same plus a prior-odds
correction that raises both thresholds by log2(N / 50k), since the prior odds of a candidate pair fall as 1/N
(derived, not tuned).

Each run is its own process so peak RSS (ru_maxrss) belongs to that size only. Data come from the hard
generator with a benchmark-only seed (NOT the dev or held-out seeds). The matcher configuration is the one frozen on
dev (read from the held-out ledger), so nothing is tuned here. Stage timings replicate FellegiSunterMatcher.match()
step by step; at the smallest size the script also runs the real match() and asserts the same candidate and merge
counts, so the staged numbers cannot drift from the shipped matcher.

Writes outputs/scale_benchmark.csv and outputs/scale_benchmark.json. A size that fails (out of memory, timeout) is
recorded as a failure row, not hidden.
"""

from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

BENCH_SEED = 777_001  # not the dev (101) or held-out seeds
RECORDS_PER_ENTITY = 1.2935  # measured on the dev-hard generator (5171 records / 4000 entities)
TIMEOUT_S = 3 * 3600


def frozen_fs_kwargs() -> dict:
    """The dev-frozen Fellegi-Sunter configuration, taken from the ledger (thresholds were chosen on dev only)."""
    from customer_master.heldout import read_ledger

    for entry in read_ledger().values():
        cfg = entry["config"]
        if cfg["matcher"] == "fellegi-sunter" and cfg["schema"] == "customer":
            keys = ("t_merge", "t_review", "name_missing_policy", "conflict_veto", "em_mode")
            return {k: cfg[k] for k in keys}
    raise RuntimeError("no frozen fellegi-sunter config in outputs/heldout_ledger.json")


SCALABLE_VARIANTS = ("scalable", "scalable-frozen", "scalable-frozen-prior")
MEM_LIMIT_GB = 6  # address-space cap so a blow-up becomes a recorded MemoryError, not a killed container


def worker(n_records: int, variant: str = "standard") -> dict:
    import numpy as np

    limit = MEM_LIMIT_GB * 2**30
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

    from customer_master.blocking import generate_candidates
    from customer_master.datasets import load_raw_sources, standardize_frame
    from customer_master.evaluation import evaluate_clusters
    from customer_master.generate_hard import generate_hard
    from customer_master.matchers.base import MERGE, PAIR_COLUMNS
    from customer_master.matchers.common import evidence_string, ordered
    from customer_master.matchers.fellegi_sunter import FellegiSunterMatcher
    from customer_master.schema import CUSTOMER

    stage: dict[str, float] = {}

    def tick(name, t0):
        stage[name] = round(time.perf_counter() - t0, 2)

    t0 = time.perf_counter()
    n_entities = max(100, round(n_records / RECORDS_PER_ENTITY))
    with tempfile.TemporaryDirectory() as tmp:
        generate_hard(Path(tmp), seed=BENCH_SEED, n_entities=n_entities)
        tick("generate", t0)
        t0 = time.perf_counter()
        raw = load_raw_sources(Path(tmp))
        clean = standardize_frame(raw)
        del raw
        tick("standardize", t0)
        import pandas as pd

        truth = pd.read_csv(Path(tmp) / "answer_key.csv", dtype=str)
    entity_of = dict(zip(truth.record_id, truth.entity_id))
    del truth

    if variant in SCALABLE_VARIANTS:
        return _worker_scalable(n_records, n_entities, clean, entity_of, stage, variant)
    matcher = FellegiSunterMatcher(CUSTOMER, **frozen_fs_kwargs())
    schema = CUSTOMER

    t0 = time.perf_counter()
    feats = schema.featurize(clean)
    tick("featurize", t0)
    t0 = time.perf_counter()
    cand, bstats = generate_candidates(feats, schema.block_rules)
    tick("block", t0)
    t0 = time.perf_counter()
    rows = [(i, j, mask, schema.compare(feats[i], feats[j])) for (i, j), mask in cand.items()]
    del cand
    tick("compare", t0)
    t0 = time.perf_counter()
    fit_rows = [r for r in rows if not (matcher.conflict_veto and matcher._conflict(r[3]))]
    matcher.fit(feats, fit_rows)
    del fit_rows
    tick("fit_em", t0)
    t0 = time.perf_counter()
    table = matcher.weight_table()
    out = []
    for i, j, _, pat in rows:
        conflict = matcher.conflict_veto and matcher._conflict(pat)
        w = matcher.weight(matcher.without_names(pat) if conflict else pat, table)
        a, b = ordered(feats, i, j)
        decision, guard_ok = matcher.decide(w, pat)
        out.append((a, b, w, decision, evidence_string(schema, pat), guard_ok))
    pairs = pd.DataFrame(out, columns=PAIR_COLUMNS)
    del out, rows
    tick("score", t0)
    t0 = time.perf_counter()
    merged = list(zip(pairs[pairs.decision == MERGE].a_id, pairs[pairs.decision == MERGE].b_id))
    m = evaluate_clusters(list(clean.record_id), entity_of, merged)
    tick("evaluate", t0)

    matching_s = sum(stage[k] for k in ("featurize", "block", "compare", "fit_em", "score"))
    result = {
        "variant": "standard",
        "target_records": n_records,
        "records": len(clean),
        "n_entities": n_entities,
        "candidate_pairs": len(pairs),
        "all_pairs_would_be": len(clean) * (len(clean) - 1) // 2,
        "reduction_ratio": round(bstats.reduction_ratio, 6),
        "skipped_blocks": bstats.per_rule_skipped_blocks,
        "skipped_block_records": bstats.per_rule_skipped_records,
        "merged_pairs": len(merged),
        "review_pairs": int((pairs.decision == "review").sum()),
        "precision": round(m["precision"], 4),
        "recall": round(m["recall"], 4),
        "f1": round(m["f1"], 4),
        "matching_s": round(matching_s, 2),
        "pairs_per_s": round(len(pairs) / max(stage["compare"] + stage["score"], 1e-9)),
        "stage_s": stage,
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024),
        "status": "ok",
        "python": sys.version.split()[0],
        "numpy": np.__version__,
    }
    if n_records <= 10_000:  # cross-check the staged replica against the shipped matcher
        real = FellegiSunterMatcher(CUSTOMER, **frozen_fs_kwargs()).match(clean)
        assert real.n_candidates == len(pairs), (real.n_candidates, len(pairs))
        assert len(real.merged()) == len(merged), (len(real.merged()), len(merged))
        result["crosscheck_vs_match"] = "identical candidate and merge counts"
    return result


FIT_EXTRACT_RECORDS = 50_000
FIT_SEED = BENCH_SEED + 1  # independent of the benchmark data: a "representative earlier extract"


def _worker_scalable(n_records, n_entities, clean, entity_of, stage, variant) -> dict:
    import numpy as np

    from customer_master.datasets import load_raw_sources, standardize_frame
    from customer_master.evaluation import evaluate_clusters
    from customer_master.generate_hard import generate_hard
    from customer_master.matchers.fs_scalable import ScalableFellegiSunter
    from customer_master.schema import CUSTOMER

    prior = variant == "scalable-frozen-prior"
    matcher = ScalableFellegiSunter(CUSTOMER, prior_correction=prior, **frozen_fs_kwargs())
    if variant != "scalable":  # frozen variants fit m/u once on an independent extract
        t0 = time.perf_counter()
        with tempfile.TemporaryDirectory() as tmp:
            generate_hard(Path(tmp), seed=FIT_SEED, n_entities=round(FIT_EXTRACT_RECORDS / RECORDS_PER_ENTITY))
            reference = standardize_frame(load_raw_sources(Path(tmp)))
        matcher.fit_on(reference)
        del reference
        stage["fit_extract_s"] = round(time.perf_counter() - t0, 2)
    out = matcher.match(clean)
    stage.update(out.info["stage_s"])
    t0 = time.perf_counter()
    merged = out.merged()
    m = evaluate_clusters(list(clean.record_id), entity_of, merged)
    stage["evaluate"] = round(time.perf_counter() - t0, 2)
    blocking = out.info["blocking"]
    return {
        "variant": variant,
        "target_records": n_records,
        "records": len(clean),
        "n_entities": n_entities,
        "candidate_pairs": out.n_candidates,
        "all_pairs_would_be": len(clean) * (len(clean) - 1) // 2,
        "reduction_ratio": round(blocking["reduction_ratio"], 6),
        "skipped_blocks": blocking["skipped_blocks"],
        "skipped_block_records": blocking["skipped_records"],
        "merged_pairs": len(merged),
        "review_pairs": int((out.pairs.decision == "review").sum()),
        "distinct_patterns": out.info["distinct_patterns"],
        "precision": round(m["precision"], 4),
        "recall": round(m["recall"], 4),
        "f1": round(m["f1"], 4),
        "matching_s": round(out.runtime_s, 2),
        "pairs_per_s": round(out.n_candidates / max(stage["compare_s"], 1e-9)),
        "threshold_shift_bits": out.info.get("threshold_shift_bits", 0.0),
        "stage_s": stage,
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024),
        "status": "ok",
        "python": sys.version.split()[0],
        "numpy": np.__version__,
    }


def orchestrate(sizes: list[int], variants: list[str]) -> None:
    json_path = ROOT / "outputs" / "scale_benchmark.json"
    previous = json.loads(json_path.read_text(encoding="utf-8")) if json_path.exists() else []
    done = {(r.get("variant", "standard"), r["target_records"]): r for r in previous}  # un-re-run rows are kept
    rows = []
    for n, variant in [(n, v) for n in sizes for v in variants]:
        print(f"== {n} records, {variant} ==", flush=True)
        t0 = time.time()
        proc = subprocess.run(
            [sys.executable, "-I", __file__, "--worker", str(n), variant],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
        wall = round(time.time() - t0, 1)
        line = next((ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith("{")), None)
        if proc.returncode == 0 and line:
            row = json.loads(line)
            if row["variant"] != variant:
                raise RuntimeError(f"worker ran {row['variant']!r} but {variant!r} was requested")
            row["wall_s"] = wall
        else:
            row = {
                "variant": variant,
                "target_records": n,
                "status": f"FAILED (exit {proc.returncode}; -9 = killed, usually out of memory)",
                "wall_s": wall,
                "stderr_tail": proc.stderr[-600:].replace(str(ROOT), "<repo>"),  # no local paths in committed outputs
            }
        print(json.dumps({k: v for k, v in row.items() if k != "stderr_tail"}), flush=True)
        done[(variant, n)] = row
        rows = [done[k] for k in sorted(done, key=lambda k: (k[1], k[0]))]
        json_path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    import pandas as pd

    nested = ("stage_s", "skipped_blocks", "skipped_block_records", "stderr_tail")
    flat = []
    for r in rows:
        f = {k: v for k, v in r.items() if k not in nested}
        for k, v in (r.get("stage_s") or {}).items():
            f[f"{k}_s"] = v
        f["skipped_blocks_total"] = sum((r.get("skipped_blocks") or {}).values())
        flat.append(f)
    pd.DataFrame(flat).to_csv(ROOT / "outputs" / "scale_benchmark.csv", index=False)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[5_000, 50_000, 500_000])
    variants = ["standard", "scalable", "scalable-frozen", "scalable-frozen-prior"]
    ap.add_argument("--variants", nargs="+", default=variants, choices=variants)
    ap.add_argument("--worker", nargs="+", metavar=("N", "VARIANT"))
    args = ap.parse_args()
    if args.worker:
        print(json.dumps(worker(int(args.worker[0]), args.worker[1] if len(args.worker) > 1 else "standard")))
    else:
        orchestrate(args.sizes, args.variants)
