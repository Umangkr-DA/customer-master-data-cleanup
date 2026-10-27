"""Phase 5: does incremental matching reproduce a full rebuild, and how much faster is it?

    python scripts/run_incremental.py

Uses the DEV-hard dataset (never the held-out set) and the dev-frozen Fellegi-Sunter configuration. 80% of the records
are the "existing" customer base, the remaining 20% arrive in 4 batches of 5%. Three results are compared:

  incremental      bootstrap on the base, then add_records() per batch (weights frozen at bootstrap)
  rebuild-frozen   full candidate generation over all records, SAME frozen weights  -> must equal incremental
  rebuild-refit    the normal full pipeline run: EM re-estimates m/u on all records -> differs a little (explained)

Quality against the answer key is computed by evaluation code only. Writes outputs/incremental_check.json.
"""

from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from customer_master.common import OUTPUT_DIR  # noqa: E402
from customer_master.datasets import load_dataset  # noqa: E402
from customer_master.evaluation import evaluate_clusters  # noqa: E402
from customer_master.experiments import entity_map  # noqa: E402
from customer_master.incremental import IncrementalMatcher, rebuild_frozen  # noqa: E402
from customer_master.matchers.base import MERGE  # noqa: E402
from customer_master.matchers.fellegi_sunter import FellegiSunterMatcher  # noqa: E402
from customer_master.schema import CUSTOMER  # noqa: E402

SPLIT_SEED = 5
BASE_FRACTION = 0.80
N_BATCHES = 4


def frozen_kwargs() -> dict:
    from customer_master.heldout import read_ledger

    for entry in read_ledger().values():
        cfg = entry["config"]
        if cfg["matcher"] == "fellegi-sunter" and cfg["schema"] == "customer":
            keys = ("t_merge", "t_review", "name_missing_policy", "conflict_veto", "em_mode")
            return {k: cfg[k] for k in keys}
    raise RuntimeError("no frozen fellegi-sunter config in outputs/heldout_ledger.json")


def quality(ids, entity_of, merged) -> dict:
    m = evaluate_clusters(ids, entity_of, list(merged))
    return {k: round(m[k], 4) for k in ("precision", "recall", "f1")} | {
        "merged_pairs": len(merged),
        "over_merged_clusters": m["over_merged_clusters"],
    }


def main() -> None:
    ds = load_dataset("dev")
    clean = ds.clean
    entity_of = entity_map(ds)
    ids = list(clean.record_id)
    kw = frozen_kwargs()

    order = ids[:]
    random.Random(SPLIT_SEED).shuffle(order)
    n_base = round(BASE_FRACTION * len(order))
    step = (len(order) - n_base) // N_BATCHES
    chunks = [order[:n_base]] + [
        order[n_base + k * step : n_base + (k + 1) * step if k < N_BATCHES - 1 else len(order)]
        for k in range(N_BATCHES)
    ]
    parts = [clean[clean.record_id.isin(c)] for c in chunks]

    matcher = FellegiSunterMatcher(CUSTOMER, **kw)
    t0 = time.perf_counter()
    inc = IncrementalMatcher.bootstrap(parts[0], matcher)
    bootstrap_s = time.perf_counter() - t0
    batches, crossed = [], 0
    for k, part in enumerate(parts[1:], 1):
        res = inc.add_records(part)
        crossed += res.cap_crossed_blocks
        batches.append(
            {
                "batch": k,
                "new_records": len(part),
                "scored_pairs": len(res.new_pairs),
                "merged_pairs": len(res.merged_pairs),
                "clusters_touched": len(res.touched_labels),
                "clusters_retired": len(res.retired_labels),
                "cap_crossed_blocks": res.cap_crossed_blocks,
                "runtime_s": round(res.runtime_s, 3),
            }
        )
    inc_merged = set(inc.merged_pairs)

    t0 = time.perf_counter()
    ref = rebuild_frozen(clean, matcher)
    frozen_s = time.perf_counter() - t0
    frozen_merged = set(zip(ref[ref.decision == MERGE].a_id, ref[ref.decision == MERGE].b_id))

    refit = FellegiSunterMatcher(CUSTOMER, **kw)
    t0 = time.perf_counter()
    refit_out = refit.match(clean)
    refit_s = time.perf_counter() - t0
    refit_merged = set(refit_out.merged())

    total_inc_s = sum(b["runtime_s"] for b in batches)
    result = {
        "dataset": "dev",
        "config": kw,
        "split": {"base_records": len(parts[0]), "batches": [len(p) for p in parts[1:]], "seed": SPLIT_SEED},
        "incremental_equals_rebuild_frozen": inc_merged == frozen_merged,
        "cap_crossed_blocks_total": crossed,
        "pairs_only_in_incremental_vs_frozen_rebuild": len(inc_merged - frozen_merged),
        "pairs_only_in_frozen_rebuild": len(frozen_merged - inc_merged),
        "vs_refit": {
            "pairs_only_in_incremental": len(inc_merged - refit_merged),
            "pairs_only_in_refit": len(refit_merged - inc_merged),
            "jaccard": round(len(inc_merged & refit_merged) / max(len(inc_merged | refit_merged), 1), 4),
        },
        "quality": {
            "incremental": quality(ids, entity_of, inc_merged),
            "rebuild_frozen": quality(ids, entity_of, frozen_merged),
            "rebuild_refit": quality(ids, entity_of, refit_merged),
        },
        "runtime_s": {
            "bootstrap_on_base": round(bootstrap_s, 2),
            "incremental_all_batches": round(total_inc_s, 2),
            "incremental_per_batch_mean": round(total_inc_s / N_BATCHES, 3),
            "rebuild_frozen_weights": round(frozen_s, 2),
            "rebuild_refit_full_pipeline": round(refit_s, 2),
        },
        "batches": batches,
    }
    out = OUTPUT_DIR / "incremental_check.json"
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
