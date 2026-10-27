"""Business impact estimate: measured rates x explicit assumptions.

Two kinds of input, kept apart on purpose:

  rates        MEASURED on synthetic data by scripts/run_phase6.py (duplicates removed, errors, review workload,
               contact reachability). They are per source record, so they scale to any customer-base size.
  assumptions  ASSUMED, read from config/assumptions.json (message volume, unit costs, analyst time). Nothing in this
               module hard-codes a business number.

estimate() is a pure function so it can be unit tested and re-run with different assumptions in the sensitivity table.
The result is an illustration of how the pieces combine, not a forecast: the data are synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

from customer_master.common import PROJECT_ROOT

ASSUMPTIONS_PATH = PROJECT_ROOT / "config" / "assumptions.json"
REQUIRED = {
    "scenario": ["customer_base_records"],
    "communication": [
        "messages_per_record_per_year",
        "share_of_messages_reaching_duplicates",
        "cost_per_message_inr",
    ],
    "errors": ["cost_per_wrong_merge_inr", "records_affected_per_over_merged_cluster"],
    "review": [
        "reviewer_minutes_per_pair",
        "reviewer_cost_per_hour_inr",
        "review_checkpoint_pairs_per_1000_records",
        "reviewer_accuracy",
    ],
    "sensitivity": ["relative_change"],
}
FRACTIONS = {"share_of_messages_reaching_duplicates", "relative_change", "reviewer_accuracy"}
SELECTORS = {"reviewer_accuracy", "relative_change", "customer_base_records"}  # not varied in sensitivity()


def load_assumptions(path: Path = ASSUMPTIONS_PATH) -> dict:
    """Read and validate the config; returns {name: value} (flat) and fails loudly on a missing or silly value."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    flat: dict = {}
    for section, names in REQUIRED.items():
        for name in names:
            try:
                value = raw[section][name]["value"]
            except KeyError as exc:
                raise ValueError(f"assumption {section}.{name} is missing from {path}") from exc
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise ValueError(f"assumption {section}.{name} must be a non-negative number, got {value!r}")
            if name in FRACTIONS and value > 1:
                raise ValueError(f"assumption {section}.{name} is a fraction and must be <= 1, got {value!r}")
            flat[name] = value
    return flat


def estimate(rates: dict, a: dict) -> dict:
    """Annual impact for a customer base of a['customer_base_records'] source records.

    rates (all per ONE source record):
      duplicates_removed_correct   duplicate records correctly merged away
      duplicates_missed            duplicate records left unmerged
      over_merged_clusters         clusters that mix customers
      review_extra_dups_per_pair   extra true duplicates found per pair reviewed (simulated reviewer)
      review_extra_overmerges_per_pair  extra over-merged clusters per pair reviewed (a wrong "match" verdict)
    """
    n = a["customer_base_records"]
    removed = rates["duplicates_removed_correct"] * n
    missed = rates["duplicates_missed"] * n
    wrong = rates["over_merged_clusters"] * n
    msgs_per_dup = a["messages_per_record_per_year"] * a["share_of_messages_reaching_duplicates"]
    cost = a["cost_per_message_inr"]

    messages_avoided = removed * msgs_per_dup
    messages_still_duplicated = missed * msgs_per_dup
    saving = messages_avoided * cost
    wrong_merge_cost = wrong * a["cost_per_wrong_merge_inr"]
    misdirected_msgs = wrong * a["records_affected_per_over_merged_cluster"] * a["messages_per_record_per_year"]

    pairs_reviewed = a["review_checkpoint_pairs_per_1000_records"] * n / 1000
    review_cost = pairs_reviewed * a["reviewer_minutes_per_pair"] / 60 * a["reviewer_cost_per_hour_inr"]
    review_extra_dups = pairs_reviewed * rates["review_extra_dups_per_pair"]
    review_saving = review_extra_dups * msgs_per_dup * cost
    review_errors = pairs_reviewed * rates["review_extra_overmerges_per_pair"]
    review_error_cost = review_errors * a["cost_per_wrong_merge_inr"]

    return {
        "customer_base_records": n,
        "duplicates_removed": round(removed),
        "duplicates_missed": round(missed),
        "over_merged_clusters": round(wrong),
        "duplicate_messages_avoided_per_year": round(messages_avoided),
        "duplicate_messages_still_sent_per_year": round(messages_still_duplicated),
        "gross_saving_inr_per_year": round(saving),
        "wrong_merge_cost_inr_per_year": round(wrong_merge_cost),
        "messages_potentially_misdirected_per_year": round(misdirected_msgs),
        "net_saving_without_review_inr_per_year": round(saving - wrong_merge_cost),
        "review_pairs": round(pairs_reviewed),
        "review_cost_inr": round(review_cost),
        "review_extra_duplicates_found": round(review_extra_dups),
        "review_saving_inr_per_year": round(review_saving),
        "review_extra_over_merged_clusters": round(review_errors),
        "review_error_cost_inr": round(review_error_cost),
        "net_saving_with_review_inr_per_year": round(
            saving - wrong_merge_cost + review_saving - review_cost - review_error_cost
        ),
        "review_pays_for_itself": bool(review_saving > review_cost + review_error_cost),
        # assumption-free-ish thresholds: how large the unit values would have to be for each step to break even
        "break_even_cost_per_wrong_merge_inr": round(saving / wrong, 2) if wrong else None,
        "break_even_value_per_extra_duplicate_inr": (
            round((review_cost + review_error_cost) / review_extra_dups, 2) if review_extra_dups else None
        ),
    }


def sensitivity(rates: dict, a: dict, output: str = "net_saving_with_review_inr_per_year") -> list:
    """One-at-a-time: move each assumption down and up by relative_change; report the output."""
    delta = a["relative_change"]
    base = estimate(rates, a)[output]
    rows = []
    for name in a:
        if name in SELECTORS:
            continue
        lo, hi = {}, {}
        for tag, factor, store in (("low", 1 - delta, lo), ("high", 1 + delta, hi)):
            changed = dict(a)
            value = a[name] * factor
            changed[name] = min(value, 1.0) if name in FRACTIONS else value
            store["value"] = changed[name]
            store["result"] = estimate(rates, changed)[output]
        rows.append(
            {
                "assumption": name,
                "base_value": a[name],
                "low_value": lo["value"],
                "high_value": hi["value"],
                f"{output}_low": lo["result"],
                f"{output}_base": base,
                f"{output}_high": hi["result"],
                "swing": abs(hi["result"] - lo["result"]),
            }
        )
    return sorted(rows, key=lambda r: -r["swing"])


# ----------------------------------------------------------------------------- interactive what-if scenario
SCENARIO_DEFAULTS = {
    "records": 100_000,
    "duplicate_rate": 0.15,  # share of records that are redundant copies of another record
    "correction_cost_inr": 40.0,  # one duplicate fixed by hand (about 6 analyst minutes at 400 INR/hour)
    "auto_resolve_rate": 0.74,  # duplicates merged automatically (held-out recall of the frozen matcher)
    "false_merge_rate": 0.035,  # automatic merges joining two different customers (1 - held-out precision)
    "ambiguous_share": 0.10,  # share of duplicates sent to a human reviewer
    "review_cost_inr": 10.0,  # one pair judged (1.5 minutes at 400 INR/hour)
    "reviewer_accuracy": 0.95,
    "wrong_merge_cost_inr": 200.0,
}


def simulate_scenario(
    records: float,
    duplicate_rate: float,
    correction_cost_inr: float,
    auto_resolve_rate: float,
    false_merge_rate: float,
    ambiguous_share: float,
    review_cost_inr: float,
    reviewer_accuracy: float,
    wrong_merge_cost_inr: float,
) -> dict:
    """HYPOTHETICAL scenario cost of cleaning `records` customer records. Not a forecast, not a verified saving.

    Baseline: every duplicate is found and fixed by hand.
        baseline_cost = duplicates x correction_cost
    With the pipeline:
        auto-resolved   = duplicates x auto_resolve_rate        (merged by the matcher, no human time)
        sent to review  = duplicates x ambiguous_share          (capped so auto + review never exceed all duplicates)
        review fixes    = sent_to_review x reviewer_accuracy    (the rest of a wrong verdict stays unresolved)
        left for later  = duplicates - auto - review fixes      (still fixed by hand at correction_cost)
        wrong merges    = auto-resolved x false_merge_rate + reviewed x (1 - reviewer_accuracy)
        pipeline_cost   = left x correction_cost + reviewed x review_cost + wrong_merges x wrong_merge_cost
    """
    for name, value in (
        ("records", records), ("correction_cost_inr", correction_cost_inr), ("review_cost_inr", review_cost_inr),
        ("wrong_merge_cost_inr", wrong_merge_cost_inr),
    ):  # fmt: skip
        if value < 0:
            raise ValueError(f"{name} must be non-negative, got {value!r}")
    for name, value in (
        ("duplicate_rate", duplicate_rate), ("auto_resolve_rate", auto_resolve_rate),
        ("false_merge_rate", false_merge_rate), ("ambiguous_share", ambiguous_share),
        ("reviewer_accuracy", reviewer_accuracy),
    ):  # fmt: skip
        if not 0 <= value <= 1:
            raise ValueError(f"{name} must be between 0 and 1, got {value!r}")

    duplicates = records * duplicate_rate
    auto = duplicates * auto_resolve_rate
    reviewed = min(duplicates * ambiguous_share, duplicates - auto)
    review_fixed = reviewed * reviewer_accuracy
    left = duplicates - auto - review_fixed
    wrong = auto * false_merge_rate + reviewed * (1 - reviewer_accuracy)

    baseline = duplicates * correction_cost_inr
    review_cost = reviewed * review_cost_inr
    wrong_cost = wrong * wrong_merge_cost_inr
    manual_left_cost = left * correction_cost_inr
    pipeline = manual_left_cost + review_cost + wrong_cost
    # wrong-merge cost per wrong merge at which the pipeline stops being cheaper than doing everything by hand
    saved_without_wrong = baseline - manual_left_cost - review_cost
    break_even = saved_without_wrong / wrong if wrong else None
    return {
        "duplicates": round(duplicates),
        "auto_resolved": round(auto),
        "sent_to_review": round(reviewed),
        "left_for_manual_fix": round(left),
        "wrong_merges": round(wrong),
        "baseline_manual_cost_inr": round(baseline),
        "manual_fix_cost_inr": round(manual_left_cost),
        "review_cost_inr": round(review_cost),
        "wrong_merge_cost_inr": round(wrong_cost),
        "pipeline_cost_inr": round(pipeline),
        "scenario_saving_inr": round(baseline - pipeline),
        "break_even_wrong_merge_cost_inr": None if break_even is None else round(break_even, 2),
        "label": "hypothetical scenario estimate, not a verified saving",
    }


def scenario_grid(base: dict, duplicate_rates, ambiguous_shares) -> list:
    """Scenario saving for every duplicate-rate x ambiguous-share combination; other inputs stay at `base`."""
    rows = []
    for d in duplicate_rates:
        for s in ambiguous_shares:
            params = {**base, "duplicate_rate": d, "ambiguous_share": s}
            saving = simulate_scenario(**params)["scenario_saving_inr"]
            rows.append({"duplicate_rate": d, "ambiguous_share": s, "scenario_saving_inr": saving})
    return rows
