"""Human-in-the-loop review queue.

Built from a matcher's scored pairs only; it never sees the answer key (the simulation of a reviewer lives in
evaluation_sim.py).

Queue membership: every candidate pair in the review band, plus every candidate pair where a name is blank
(whatever its decision), minus pairs whose two records are already in the same auto-merged cluster (reviewing those
changes nothing).

Ranking: expected value of review (EV) = expected number of correct pair decisions gained by judging this pair.
  q = posterior probability that the pair is a match, from the Fellegi-Sunter weight W and a prior p:
      q = 1 / (1 + 2^-(W + log2(p / (1-p)))).
  The prior is estimated by EM on the candidate pairs themselves (no labels), SEPARATELY for three strata: both names
  agree, a name is blank, and the names conflict. Conflicting-name pairs are scored on the other fields only (the
  matcher vetoes them), so pooling them with the rest would give households that share phone and e-mail a
  near-certain q.
  not merged now : EV = q        merged now : EV = 1 - q (catching a false merge; blank-name audit rows only)
"conflicts_last" is the stratified EV with every conflicting-name pair listed after all other pairs (the matcher vetoes
these pairs, so a name conflict is treated as strong evidence against; this is an ordinal rule, no constant is tuned).
Variants tried on dev (ranking="global", "size_weighted_*" = EV multiplied by |A|*|B|, the cross pairs a true merge
would recover) are kept so the choice can be reproduced; see scripts/run_phase4.py.
"""

from __future__ import annotations

import math

import pandas as pd

from customer_master.clustering import cluster
from customer_master.matchers.base import MERGE, REVIEW, MatchOutput

QUEUE_COLUMNS = [
    "rank", "a_id", "b_id", "score", "match_probability", "expected_value", "current_decision", "reason_in_queue",
    "cluster_size_a", "cluster_size_b", "agree_fields", "disagree_fields", "missing_fields", "levels",
    "a_full_name", "b_full_name", "a_phone", "b_phone", "a_email", "b_email", "a_city", "b_city", "a_pincode",
    "b_pincode", "a_signup_date", "b_signup_date",
]  # fmt: skip
_VALUE_COLUMNS = ["full_name", "phone", "email", "city", "pincode", "signup_date"]


def estimate_prior(weights, iterations: int = 100) -> float:
    """Mixing proportion P(match | candidate) by EM with the weights held fixed (log2 likelihood ratios)."""
    w = [float(x) for x in weights]
    if not w:
        return 0.5
    p = 0.1
    for _ in range(iterations):
        logit = math.log(p / (1 - p))
        post = [1 / (1 + math.exp(-min(50.0, max(-50.0, math.log(2) * x + logit)))) for x in w]
        new = min(max(sum(post) / len(post), 1e-6), 0.9)
        if abs(new - p) < 1e-9:
            break
        p = new
    return p


def posterior(weight: float, prior: float) -> float:
    z = math.log(2) * weight + math.log(prior / (1 - prior))
    return 1 / (1 + math.exp(-min(50.0, max(-50.0, z))))


def parse_levels(evidence: str) -> dict:
    out = {}
    for token in evidence.split():
        name, lvl = token.split("=")
        out[name] = None if lvl == "-" else int(lvl)
    return out


RANKINGS = ("stratified", "global", "size_weighted_global", "size_weighted_stratified", "conflicts_last")


def name_stratum(levels: dict) -> str:
    if levels.get("first") == 0 or levels.get("last") == 0:
        return "conflict"
    if levels.get("first") is None or levels.get("last") is None:
        return "blank"
    return "agree"


def build_review_queue(clean: pd.DataFrame, out: MatchOutput, ranking: str = "stratified") -> pd.DataFrame:
    if ranking not in RANKINGS:
        raise ValueError(f"ranking must be one of {RANKINGS}")
    pairs = out.pairs
    if pairs.empty:
        return pd.DataFrame(columns=QUEUE_COLUMNS)
    ids = list(clean.record_id)
    labels = cluster(ids, out.merged())
    size: dict = {}
    for lab in labels.values():
        size[lab] = size.get(lab, 0) + 1
    levels = pairs.evidence.map(parse_levels)
    strata = levels.map(name_stratum)
    global_prior = estimate_prior(pairs.score)
    prior_of = {
        k: estimate_prior(pairs.score[strata == k]) for k in ("agree", "blank", "conflict") if (strata == k).any()
    }
    blank = levels.map(lambda d: d.get("first") is None or d.get("last") is None)
    in_band = pairs.decision == REVIEW
    redundant = pd.Series(
        [labels[a] == labels[b] and d != MERGE for a, b, d in zip(pairs.a_id, pairs.b_id, pairs.decision)],
        index=pairs.index,
    )
    keep = (in_band | blank) & ~redundant
    sub = pairs[keep].copy()
    lv = levels[keep]
    clean_i = clean.set_index("record_id")
    rows = []
    for (idx, r), lvl in zip(sub.iterrows(), lv):
        use_global = ranking in ("global", "size_weighted_global")
        q = posterior(r.score, global_prior if use_global else prior_of[strata[idx]])
        sa, sb = size[labels[r.a_id]], size[labels[r.b_id]]
        merged = r.decision == MERGE
        ev = (1 - q) if merged else q
        if ranking.startswith("size_weighted") and not merged:
            ev *= sa * sb
        agree = [k for k, v in lvl.items() if v is not None and v >= 1]
        dis = [k for k, v in lvl.items() if v == 0]
        miss = [k for k, v in lvl.items() if v is None]
        reasons = []
        if r.decision == REVIEW:
            reasons.append("review band")
        if lvl.get("first") is None or lvl.get("last") is None:
            reasons.append("blank name")
        row = {
            "a_id": r.a_id, "b_id": r.b_id, "score": round(float(r.score), 3), "match_probability": round(q, 4),
            "expected_value": round(ev, 4), "current_decision": r.decision, "reason_in_queue": " + ".join(reasons),
            "cluster_size_a": sa, "cluster_size_b": sb, "agree_fields": ",".join(agree),
            "disagree_fields": ",".join(dis), "missing_fields": ",".join(miss), "levels": r.evidence,
        }  # fmt: skip
        for col in _VALUE_COLUMNS:
            row[f"a_{col}"] = clean_i.at[r.a_id, col]
            row[f"b_{col}"] = clean_i.at[r.b_id, col]
        rows.append(row)
    queue = pd.DataFrame(rows)
    if queue.empty:
        return pd.DataFrame(columns=QUEUE_COLUMNS)
    queue["_conflict"] = queue.disagree_fields.str.contains("first|last") & (ranking == "conflicts_last")
    queue = queue.sort_values(
        ["_conflict", "expected_value", "score", "a_id", "b_id"], ascending=[True, False, False, True, True]
    )
    queue.insert(0, "rank", range(1, len(queue) + 1))
    queue.attrs["priors"] = {"global": global_prior, **prior_of}
    return queue[QUEUE_COLUMNS].reset_index(drop=True)
