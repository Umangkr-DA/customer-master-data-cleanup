"""Phase 6: business impact estimate (assumptions in config/) and a data-quality monitoring run.

    python scripts/run_phase6.py

Impact (outputs/impact_estimate.json, outputs/impact_sensitivity.csv, docs/business_impact.md)
  rates        measured: matching outcomes from the frozen Fellegi-Sunter HELD-OUT ledger entry (read, not re-run),
               review rates from the DEV reviewer simulation, contact reachability on the DEV data
  assumptions  config/assumptions.json  (every business number; nothing is hard-coded here)
Monitoring: see scripts/monitor_quality.py (kept separate so it can run after every pipeline run).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from customer_master.common import OUTPUT_DIR, r2_phone, r3_email  # noqa: E402
from customer_master.datasets import load_dataset  # noqa: E402
from customer_master.heldout import read_ledger  # noqa: E402
from customer_master.impact import ASSUMPTIONS_PATH, estimate, load_assumptions, sensitivity  # noqa: E402
from customer_master.matchers.fellegi_sunter import FellegiSunterMatcher  # noqa: E402
from customer_master.survivorship import build_golden  # noqa: E402


def frozen_fs() -> tuple:
    """(ledger metrics on held-out, matcher built from the frozen config)."""
    for entry in read_ledger().values():
        cfg = entry["config"]
        is_fs = cfg["matcher"] == "fellegi-sunter" and cfg["schema"] == "customer"
        if is_fs and entry["metrics"]["dataset"] == "heldout":
            keys = ("t_merge", "t_review", "name_missing_policy", "conflict_veto", "em_mode")
            return entry["metrics"], FellegiSunterMatcher(**{k: cfg[k] for k in keys})
    raise RuntimeError("no frozen fellegi-sunter entry in the held-out ledger")


def reachable(phone, email) -> bool:
    return r2_phone(phone) or r3_email(email)


def reachability(matcher) -> dict:
    """Share of customers with a usable phone or e-mail, before and after. DEV data; no answer key involved."""
    ds = load_dataset("dev")
    out = matcher.match(ds.clean)
    golden, cmap = build_golden(ds.clean, out.merged())
    raw = ds.raw.set_index("record_id")
    raw_ok = {rid: reachable(r.phone, r.email) for rid, r in raw.iterrows()}
    clean_ok = {r.record_id: reachable(r.phone, r.email) for r in ds.clean.itertuples()}
    cust_raw = cmap.assign(ok=cmap.record_id.map(raw_ok)).groupby("golden_id").ok.any()
    cust_clean = cmap.assign(ok=cmap.record_id.map(clean_ok)).groupby("golden_id").ok.any()
    cust_gold = golden.set_index("golden_id").apply(lambda r: reachable(r.phone, r.email), axis=1)
    return {
        "dataset": "dev",
        "records": len(raw),
        "customers_after_matching": len(golden),
        "records_reachable_raw": round(sum(raw_ok.values()) / len(raw_ok), 4),
        "records_reachable_standardized": round(sum(clean_ok.values()) / len(clean_ok), 4),
        "customers_reachable_raw_any_record": round(float(cust_raw.mean()), 4),
        "customers_reachable_standardized_any_record": round(float(cust_clean.mean()), 4),
        "customers_reachable_golden": round(float(cust_gold.mean()), 4),
        "note": "reachable = a value passing the project's strict phone (R2) or e-mail (R3) rule. The raw figures are "
        "format-strict: many raw numbers fail only because of formatting that standardization fixes, so 'raw' "
        "understates what a human could use.",
    }


def review_rates(accuracy: float, per_1000: float) -> dict:
    sim = pd.read_csv(OUTPUT_DIR / "review_simulation.csv")
    comp = pd.read_csv(OUTPUT_DIR / "matcher_comparison.csv")
    dev = comp[(comp.matcher == "fellegi-sunter") & (comp.dataset == "dev")].iloc[0]
    true_pairs = dev.true_positive_pairs + dev.false_negative_pairs
    k_target = per_1000 * dev.records / 1000
    s = sim[(sim.order == "ev") & (sim.reviewer_accuracy == accuracy)]
    if s.empty:
        have = sorted(sim.reviewer_accuracy.unique())
        raise ValueError(f"reviewer_accuracy {accuracy} is not simulated; available: {have}")
    row = s.iloc[(s.reviewed - k_target).abs().argsort().iloc[0]]
    base = s[s.reviewed == 0].iloc[0]
    k = float(row.reviewed)
    return {
        "source": "outputs/review_simulation.csv (dev, SIMULATED reviewer, ranked queue)",
        "reviewed_pairs_used": k,
        "recall_before": round(float(base.recall), 4),
        "recall_after": round(float(row.recall), 4),
        "review_extra_dups_per_pair": float((row.recall - base.recall) * true_pairs / k),
        "review_extra_overmerges_per_pair": float(max(row.over_merged_clusters - base.over_merged_clusters, 0) / k),
    }


def measured_rates(held: dict, rev: dict) -> dict:
    n = held["records"]
    removed_total = n - held["predicted_clusters"]
    removed_correct = removed_total - held["over_merged_clusters"]  # each over-merged cluster holds >= 1 wrong removal
    true_redundant = n - held["true_customers"]
    return {
        "source": "held-out ledger entry of the frozen Fellegi-Sunter matcher (read from the ledger, not re-run)",
        "records": n,
        "duplicates_removed_correct": removed_correct / n,
        "duplicates_missed": (true_redundant - removed_correct) / n,
        "over_merged_clusters": held["over_merged_clusters"] / n,
        "review_extra_dups_per_pair": rev["review_extra_dups_per_pair"],
        "review_extra_overmerges_per_pair": rev["review_extra_overmerges_per_pair"],
        "approximation": "duplicates_removed_correct = records - predicted clusters - over-merged clusters (a lower "
        "bound on correct removals); duplicates_missed = true redundant records - correct removals.",
    }


def inr(x) -> str:
    return "n/a" if x is None else f"INR {x:,.0f}" if float(x).is_integer() else f"INR {x:,.2f}"


def write_doc(est: dict, a: dict, rates: dict, reach: dict, rev: dict, sens: pd.DataFrame) -> None:
    col = "net_saving_with_review_inr_per_year"
    auto_pays = est["net_saving_without_review_inr_per_year"] > 0
    review_pays = est["review_pays_for_itself"]
    per_dup = a["messages_per_record_per_year"] * a["share_of_messages_reaching_duplicates"] * a["cost_per_message_inr"]
    lines = [
        "# Business impact estimate (illustrative)",
        "",
        "Generated by `python scripts/run_phase6.py`. **Every number below is computed.** The *assumptions* are in",
        "[`config/assumptions.json`](../config/assumptions.json); they are placeholders, not facts about any",
        "company. The rates they multiply are measured on synthetic data. This shows how the pieces combine, not what",
        "a real file would save, and the outcome flips with the unit costs (see the break-even rows).",
        "",
        f"## Result for a customer file of {est['customer_base_records']:,} source records",
        "",
        "| Item | Per year |",
        "|---|---|",
        f"| Duplicate records correctly removed | {est['duplicates_removed']:,} |",
        f"| Duplicates still unmerged | {est['duplicates_missed']:,} |",
        f"| Over-merged clusters (customers mixed up) | {est['over_merged_clusters']:,} |",
        f"| Duplicate messages avoided | {est['duplicate_messages_avoided_per_year']:,} |",
        f"| Gross saving | {inr(est['gross_saving_inr_per_year'])} |",
        f"| Cost of wrong merges | {inr(est['wrong_merge_cost_inr_per_year'])} |",
        f"| **Net, auto-merge only** | **{inr(est['net_saving_without_review_inr_per_year'])}** |",
        f"| Review of {est['review_pairs']:,} pairs: analyst cost | {inr(est['review_cost_inr'])} |",
        f"| Review: extra duplicates found | {est['review_extra_duplicates_found']:,} "
        f"(worth {inr(est['review_saving_inr_per_year'])}) |",
        f"| Review: extra wrong merges | {est['review_extra_over_merged_clusters']:,} "
        f"(cost {inr(est['review_error_cost_inr'])}) |",
        f"| **Net, auto-merge + review** | **{inr(est[col])}** |",
        "",
        "## Reading it",
        "",
        f"* Auto-merge {'pays for itself' if auto_pays else 'does NOT pay for itself'} on messaging savings alone "
        f"under these assumptions. It breaks even if one wrong merge costs at most "
        f"{inr(est['break_even_cost_per_wrong_merge_inr'])} (assumed: {inr(a['cost_per_wrong_merge_inr'])}).",
        f"* Review {'pays for itself' if review_pays else 'does NOT pay for itself'} "
        f"(simulated reviewer accuracy {a['reviewer_accuracy']:.0%}). It would break even if removing one extra "
        f"duplicate were worth {inr(est['break_even_value_per_extra_duplicate_inr'])} a year "
        f"(modelled at {inr(per_dup)}).",
        "* Not modelled, and likely larger than e-mail/SMS savings: postal mail, analytics and reporting errors from "
        "double-counted customers, compliance (one customer, one consent record), and contact reachability below.",
        "",
        "## Where the rates come from (measured)",
        "",
        f"* Matching outcomes: {rates['source']}. {rates['approximation']}",
        f"* Review: {rev['source']}; {rev['reviewed_pairs_used']:.0f} pairs reviewed moved dev recall from "
        f"{rev['recall_before']:.3f} to {rev['recall_after']:.3f}.",
        "",
        "## Contact reachability (dev data, measured)",
        "",
        "| View | Share with a usable phone or e-mail |",
        "|---|---|",
        f"| Source records, as received | {reach['records_reachable_raw']:.1%} |",
        f"| Source records, standardized | {reach['records_reachable_standardized']:.1%} |",
        f"| Customers, any source record usable as received | {reach['customers_reachable_raw_any_record']:.1%} |",
        f"| Customers, golden record (standardized + survivorship) | {reach['customers_reachable_golden']:.1%} |",
        "",
        reach["note"],
        "",
        f"## Which assumptions matter (one at a time, +/-{a['relative_change']:.0%}, output: net with review)",
        "",
        "| Assumption | Low | Base | High | Net at low | Net at high |",
        "|---|---|---|---|---|---|",
    ]
    for _, r in sens.head(6).iterrows():
        lines.append(
            f"| `{r.assumption}` | {r.low_value:g} | {r.base_value:g} | {r.high_value:g} | "
            f"{r[col + '_low']:,.0f} | {r[col + '_high']:,.0f} |"
        )
    lines += ["", "Full table: [`outputs/impact_sensitivity.csv`](../outputs/impact_sensitivity.csv).", ""]
    (ROOT / "docs" / "business_impact.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    a = load_assumptions()
    held, matcher = frozen_fs()
    rev = review_rates(a["reviewer_accuracy"], a["review_checkpoint_pairs_per_1000_records"])
    rates = measured_rates(held, rev)
    reach = reachability(matcher)
    est = estimate(rates, a)
    sens = pd.DataFrame(sensitivity(rates, a))
    sens.to_csv(OUTPUT_DIR / "impact_sensitivity.csv", index=False)
    out = {
        "assumptions_file": str(ASSUMPTIONS_PATH.relative_to(ROOT)),
        "assumptions": a,
        "measured_rates": rates,
        "review_rates": rev,
        "reachability": reach,
        "estimate": est,
    }
    (OUTPUT_DIR / "impact_estimate.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    write_doc(est, a, rates, reach, rev, sens)
    print(json.dumps({"estimate": est, "reachability": reach}, indent=2))


if __name__ == "__main__":
    main()
