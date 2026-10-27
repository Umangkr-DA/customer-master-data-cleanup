"""Error analysis for the frozen Fellegi-Sunter matcher on DEV data -> outputs/case_study_evidence.json

Every number quoted in docs/case_studies.md comes from this file. DEV only: the held-out set is not inspected.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from customer_master.clustering import cluster  # noqa: E402
from customer_master.common import OUTPUT_DIR  # noqa: E402
from customer_master.comparison import tune_on_dev  # noqa: E402
from customer_master.datasets import load_dataset  # noqa: E402
from customer_master.evaluation import pairs_within  # noqa: E402
from customer_master.experiments import entity_map, run_matcher  # noqa: E402
from customer_master.survivorship import build_golden  # noqa: E402


def rec(clean, rid):
    r = clean.loc[rid]
    return {
        k: (None if pd.isna(r[k]) else r[k]) for k in ["full_name", "phone", "email", "city", "pincode", "signup_date"]
    }


def main() -> None:
    dev = load_dataset("dev")
    frozen, _ = tune_on_dev(dev)
    m = frozen["fellegi-sunter"]
    out, _ = run_matcher(m, dev)
    ent = entity_map(dev)
    clean = dev.clean.set_index("record_id")
    P = out.pairs.copy()
    P["true"] = [ent[a] == ent[b] for a, b in zip(P.a_id, P.b_id)]
    Pi = P.set_index(["a_id", "b_id"])

    groups: dict = {}
    for r, e in ent.items():
        groups.setdefault(e, []).append(r)
    true_pairs = pairs_within(groups)
    labels = cluster(list(clean.index), out.merged())
    fn = sorted(p for p in true_pairs if labels[p[0]] != labels[p[1]])
    cand = set(Pi.index)
    ev: dict = {"matcher_config": m.config(), "dev_false_negative_pairs": len(fn)}
    ev["fn_by_stage"] = {
        "never_a_candidate (blocking)": sum(p not in cand for p in fn),
        "scored_in_review_band": sum(p in cand and Pi.loc[p, "decision"] == "review" for p in fn),
        "scored_below_review_band": sum(p in cand and Pi.loc[p, "decision"] == "non-match" for p in fn),
    }

    def same(a, b, col):
        return (
            clean.loc[a, col] is not None and not pd.isna(clean.loc[a, col]) and clean.loc[a, col] == clean.loc[b, col]
        )

    def blank_name(a, b):
        return pd.isna(clean.loc[a, "full_name"]) or pd.isna(clean.loc[b, "full_name"])

    both = [p for p in fn if p in cand and same(*p, "phone") and same(*p, "email")]
    ev["fn_phone_and_email_identical"] = {
        "pairs": len(both),
        "of_which_a_name_blank": sum(blank_name(*p) for p in both),
    }
    cls = [(a, b) for a, b in cand if same(a, b, "phone") and same(a, b, "email") and blank_name(a, b)]
    ev["candidates_phone_email_identical_and_blank_name"] = {
        "pairs": len(cls),
        "truly_same_customer": int(sum(ent[a] == ent[b] for a, b in cls)),
    }
    nc = [p for p in fn if p not in cand]
    ev["never_candidate_phone_and_email_both_differ_or_blank"] = sum(
        not same(*p, "phone") and not same(*p, "email") for p in nc
    )

    fp = P[(P.decision == "merge") & (~P.true)]
    ev["false_merges"] = {
        "pairs": len(fp),
        "same_phone": int(sum(same(a, b, "phone") for a, b in zip(fp.a_id, fp.b_id))),
        "identical_name": int(
            sum(clean.loc[a, "full_name"] == clean.loc[b, "full_name"] for a, b in zip(fp.a_id, fp.b_id))
        ),
    }
    ev["examples"] = {}
    for key, (a, b) in {
        "missed_blank_name": ("CRM-01441", "SALES-00790"),
        "false_merge_phone_off_by_one": ("SALES-00919", "WEB-01748"),
        "false_merge_namesake_household": ("CRM-00131", "SALES-01324"),
        "missed_contacts_changed": ("CRM-00240", "SALES-01215"),
    }.items():
        row = Pi.loc[(a, b)] if (a, b) in cand else None
        ev["examples"][key] = {
            "pair": [a, b],
            "same_customer": ent[a] == ent[b],
            "in_candidates": (a, b) in cand,
            "score": None if row is None else round(float(row.score), 2),
            "decision": None if row is None else row.decision,
            "evidence": None if row is None else row.evidence,
            "records": {a: rec(clean, a), b: rec(clean, b)},
        }

    golden, cmap = build_golden(dev.clean, out.merged())
    cm = cmap.merge(dev.clean[["record_id", "full_name"]], on="record_id")
    gname = dict(zip(golden.golden_id, golden.full_name))
    # the previous name rule (longest valid name; ties CRM > Sales > Web > record_id), replayed on the same clusters
    prio = {"CRM": 1, "Sales": 2, "Web": 3}
    sysof = dict(zip(dev.clean.record_id, dev.clean.source_system))
    old_name = {}
    for gid, d in cm.groupby("golden_id"):
        cands = sorted((n, r) for n, r in zip(d.full_name, d.record_id) if n)
        if cands:
            old_name[gid] = sorted(cands, key=lambda x: (-len(x[0]), prio[sysof[x[1]]], x[1]))[0][0]
    pure_multi, differs_from_mode, old_differs = 0, 0, 0
    swap = 0
    surprise = None
    for gid, d in cm.groupby("golden_id"):
        if len(d) < 2 or len({ent[r] for r in d.record_id}) != 1:
            continue
        names = [n for n in d.full_name if n]
        if len(set(names)) < 2:
            continue
        pure_multi += 1
        mode, cnt = Counter(names).most_common(1)[0]
        if old_name[gid] != mode and cnt > 1 and Counter(names)[old_name[gid]] == 1:
            old_differs += 1
        if gname[gid] != mode and cnt > 1 and Counter(names)[gname[gid]] == 1:
            differs_from_mode += 1
            if surprise is None or len(names) > len(surprise["names"]):
                surprise = {"golden_id": gid, "names": names, "golden_name": gname[gid], "modal_name": mode,
                            "records": list(d.record_id)}  # fmt: skip
        toks = gname[gid].split()
        if any(
            n.split() and n.split()[0] == toks[-1] and n.split()[-1] == toks[0] for n in names if n and n != gname[gid]
        ):
            swap += 1
    ev["survivorship"] = {
        "correctly_merged_clusters_with_conflicting_names": pure_multi,
        "one_off_variant_wins_while_another_spelling_appears_2plus_times_PREVIOUS_RULE": old_differs,
        "one_off_variant_wins_while_another_spelling_appears_2plus_times_CURRENT_RULE": differs_from_mode,
        "golden_name_is_reversed_order_of_another_member": swap,
        "example": surprise,
    }
    (OUTPUT_DIR / "case_study_evidence.json").write_text(json.dumps(ev, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in ev.items() if k not in ("examples", "matcher_config")}, indent=2, default=str))


if __name__ == "__main__":
    main()
