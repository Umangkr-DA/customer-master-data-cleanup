"""Matcher (c): Fellegi-Sunter probabilistic record linkage, implemented from scratch.

For each candidate pair the schema produces a comparison pattern (one agreement level per field, None = missing).
Under conditional independence the match weight is  W = sum_f log2( m[f][level] / u[f][level] )  where
  m = P(level | same customer)   u = P(level | different customers).
Missing fields contribute 0 (no information).

Estimating the parameters WITHOUT labels
  u  from random record pairs (almost all are non-matches) plus exact analytic values for exact-agreement levels.
  m  by EM on blocked candidate pairs, one "session" per blocking rule. A field used by the rule is excluded from that
     session (its agreement is guaranteed by blocking, so it would bias m upwards); m for a field is the average over
     the sessions in which it was free, weighted by the expected number of matches. This is the same idea as splink's
     recommended training procedure.

Name compatibility (domain rule, kept from the baseline's name guard): a pair whose given name or surname is present on
both sides and completely different (level 0 after trying the swapped order) is a CONFLICT. Conflicts are excluded from
EM (otherwise households that share phone + email + address would be absorbed into the "match" class and inflate
m(name differs)), and can never auto-merge: at best they go to review.

Decisions use two thresholds on W: merge (auto-merge), review (human band), else non-match. The thresholds are
chosen on DEV data by calibrate_thresholds() in experiments code and frozen in config(). The name-missing policy
keeps the project's "blank is better than wrong" principle: "phone+email" (the baseline's rule) lets a pair with a
blank name merge only if phone AND email agree exactly; "review" never auto-merges it; "none" applies no guard.
"""

from __future__ import annotations

import math
import random
from collections import Counter

import numpy as np
import pandas as pd

from customer_master.matchers.base import MERGE, NON_MATCH, PAIR_COLUMNS, REVIEW, MatchOutput, empty_pairs, timed
from customer_master.matchers.common import SHARED_CODE, candidate_patterns, code_fingerprint, evidence_string, ordered
from customer_master.schema import CUSTOMER, Schema

# fields whose top agreement level has an exact analytic u (value-collision probability)
_EXACT_TOP = {
    "customer": {"phone": "phone", "email": "email", "city": "city", "pincode": "pincode", "date": "date"},
    "febrl": {
        "soc_sec_id": "soc_sec_id", "dob": "dob", "address": "address", "suburb": "suburb", "postcode": "postcode",
        "state": "state",
    },
}  # fmt: skip
_CODE_FIELDS = {"phone", "soc_sec_id", "dob", "postcode"}  # level 1 = one typo


def _collision_prob(values: list) -> float:
    vals = [v for v in values if v]
    n = len(vals)
    if n < 2:
        return 0.0
    same = sum(c * (c - 1) / 2 for c in Counter(vals).values())
    return same / (n * (n - 1) / 2)


class FellegiSunterMatcher:
    name = "fellegi-sunter"

    def __init__(
        self,
        schema: Schema = CUSTOMER,
        t_merge: float = 12.0,
        t_review: float = 6.0,
        name_missing_policy: str = "phone+email",  # none | phone+email | review
        conflict_veto: bool = True,
        em_iterations: int = 40,
        u_sample_pairs: int = 150_000,
        seed: int = 7,
        em_mode: str = "joint",
        u_prior_strength: float = 50.0,
    ):
        self.schema, self.t_merge, self.t_review = schema, t_merge, t_review
        self.name_missing_policy, self.em_iterations = name_missing_policy, em_iterations
        self.conflict_veto = conflict_veto
        self.u_sample_pairs, self.seed = u_sample_pairs, seed
        self.em_mode, self.u_prior_strength = em_mode, u_prior_strength
        self.m: list[np.ndarray] = []
        self.u: list[np.ndarray] = []

    def config(self) -> dict:
        return {
            "matcher": self.name, "schema": self.schema.name, "t_merge": self.t_merge, "t_review": self.t_review,
            "name_missing_policy": self.name_missing_policy, "conflict_veto": self.conflict_veto,
            "em_iterations": self.em_iterations,
            "u_sample_pairs": self.u_sample_pairs, "seed": self.seed, "em_mode": self.em_mode,
            "u_prior_strength": self.u_prior_strength,
            "block_rules": [(r.name, sorted(r.fields), r.max_block) for r in self.schema.block_rules],
            "code_sha": code_fingerprint(*SHARED_CODE, "matchers/fellegi_sunter.py"),
        }  # fmt: skip

    # ------------------------------------------------------------------ parameter estimation
    def estimate_u(self, feats: list) -> list[np.ndarray]:
        schema, rng = self.schema, random.Random(self.seed)
        n, n_levels = len(feats), schema.n_levels
        counts = [np.zeros(k) for k in n_levels]
        for _ in range(self.u_sample_pairs):
            i, j = rng.randrange(n), rng.randrange(n)
            if i == j:
                continue
            for f, lvl in enumerate(schema.compare(feats[i], feats[j])):
                if lvl is not None:
                    counts[f][lvl] += 1
        u = [(c + 0.5) / (c.sum() + 0.5 * len(c)) for c in counts]
        exact = _EXACT_TOP.get(schema.name, {})
        for f, (fname, k) in enumerate(schema.fields):
            if fname not in exact:
                continue
            top = _collision_prob([x[exact[fname]] for x in feats])
            top = max(top, 1.0 / (n * n))
            rest = u[f][:-1]
            u[f] = np.append(rest / rest.sum() * (1 - top), top)
            if fname in _CODE_FIELDS and k == 3:  # level 1 = a typo of the same code: ~9*L neighbours per value
                length = np.mean([len(x[exact[fname]]) for x in feats if x[exact[fname]]])
                floor = min(9 * length * top, 0.5)
                if u[f][1] < floor:
                    u[f][1] = floor
                    u[f][0] = max(1 - u[f][1] - u[f][2], 1e-9)
        return u

    def _em_session(self, patterns: np.ndarray, counts: np.ndarray, free: list[int], u: list[np.ndarray]):
        """EM over unique patterns for the `free` fields. Returns (m for free fields, expected matches)."""
        n_levels = self.schema.n_levels
        m = {f: np.linspace(0.05, 0.8, n_levels[f]) for f in free}
        for f in free:
            m[f] = m[f] / m[f].sum()
        p = 0.05
        total = counts.sum()
        for _ in range(self.em_iterations):
            log_m = np.zeros(len(patterns))
            log_u = np.zeros(len(patterns))
            for f in free:
                obs = patterns[:, f] >= 0
                lv = np.where(obs, patterns[:, f], 0)
                log_m += np.where(obs, np.log(m[f][lv]), 0.0)
                log_u += np.where(obs, np.log(u[f][lv]), 0.0)
            a, b = np.log(p) + log_m, np.log(1 - p) + log_u
            r = 1 / (1 + np.exp(np.clip(b - a, -50, 50)))
            w = r * counts
            for f in free:
                obs = patterns[:, f] >= 0
                denom = w[obs].sum()
                if denom <= 0:
                    continue
                new = np.array([w[obs & (patterns[:, f] == lvl)].sum() for lvl in range(n_levels[f])]) + 1e-6
                m[f] = new / new.sum()
            p = float(np.clip(w.sum() / total, 1e-6, 0.9))
        return m, float(w.sum())

    def _em_joint(self, patterns: np.ndarray, counts: np.ndarray, u0: list[np.ndarray]):
        """EM over ALL candidate pairs estimating both m and u (fastLink style).

        u here is P(level | non-match AND candidate), the population the decision actually applies to, so it already
        reflects that blocked non-matches (households, namesakes) agree on more fields than random pairs do. The
        random-pair u is only the starting point, held by a weak Dirichlet prior (`u_prior_strength` pseudo-pairs).
        """
        n_levels = self.schema.n_levels
        n_f = len(n_levels)
        m = [np.linspace(0.05, 0.8, k) for k in n_levels]
        m = [x / x.sum() for x in m]
        u = [x.copy() for x in u0]
        p, total = 0.1, counts.sum()
        for _ in range(self.em_iterations):
            log_m = np.zeros(len(patterns))
            log_u = np.zeros(len(patterns))
            for f in range(n_f):
                obs = patterns[:, f] >= 0
                lv = np.where(obs, patterns[:, f], 0)
                log_m += np.where(obs, np.log(m[f][lv]), 0.0)
                log_u += np.where(obs, np.log(u[f][lv]), 0.0)
            a, b = np.log(p) + log_m, np.log(1 - p) + log_u
            r = 1 / (1 + np.exp(np.clip(b - a, -50, 50)))
            wm, wu = r * counts, (1 - r) * counts
            for f in range(n_f):
                obs = patterns[:, f] >= 0
                lv = patterns[:, f]
                new_m = np.array([wm[obs & (lv == k)].sum() for k in range(n_levels[f])]) + 1e-6
                new_u = np.array([wu[obs & (lv == k)].sum() for k in range(n_levels[f])])
                new_u = new_u + self.u_prior_strength * u0[f]
                m[f], u[f] = new_m / new_m.sum(), new_u / new_u.sum()
            p = float(np.clip(wm.sum() / total, 1e-6, 0.9))
        return m, u

    def fit(self, feats: list, rows: list) -> None:
        schema = self.schema
        n_f = len(schema.fields)
        u = self.estimate_u(feats)
        if self.em_mode == "joint":
            enc = lambda pat: [(-1 if x is None else x) for x in pat]  # noqa: E731
            cnt = Counter(tuple(enc(pat)) for _, _, _, pat in rows)
            pats = np.array(list(cnt.keys()), dtype=int)
            counts = np.array(list(cnt.values()), dtype=float)
            self.m, self.u = self._em_joint(pats, counts, u)
            return
        enc = lambda pat: [(-1 if x is None else x) for x in pat]  # noqa: E731
        sessions = []
        for r_idx, rule in enumerate(schema.block_rules):
            bit = 1 << r_idx
            cnt = Counter(tuple(enc(pat)) for _, _, mask, pat in rows if mask & bit)
            if len(cnt) < 2:
                continue
            free = [f for f, name in enumerate(schema.field_names) if name not in rule.fields]
            pats = np.array(list(cnt.keys()), dtype=int)
            counts = np.array(list(cnt.values()), dtype=float)
            m, expected = self._em_session(pats, counts, free, u)
            sessions.append((m, expected))
        final = []
        for f in range(n_f):
            acc, wsum = np.zeros(schema.n_levels[f]), 0.0
            for m, expected in sessions:
                if f in m:
                    acc += m[f] * expected
                    wsum += expected
            final.append(acc / wsum if wsum > 0 else np.full(schema.n_levels[f], 1.0 / schema.n_levels[f]))
        self.m, self.u = final, u

    def weight_table(self) -> list[np.ndarray]:
        return [np.log2(m / u) for m, u in zip(self.m, self.u)]

    def parameters(self) -> dict:
        wt = self.weight_table()
        return {
            fname: {
                "m": self.m[f].round(5).tolist(),
                "u": self.u[f].round(8).tolist(),
                "weight": wt[f].round(2).tolist(),
            }
            for f, fname in enumerate(self.schema.field_names)
        }

    # ------------------------------------------------------------------ scoring
    def weight(self, pattern: tuple, table: list | None = None) -> float:
        table = table or self.weight_table()
        return float(sum(table[f][lvl] for f, lvl in enumerate(pattern) if lvl is not None))

    def _conflict(self, pattern: tuple) -> bool:
        names = self.schema.field_names
        return pattern[names.index("first")] == 0 or pattern[names.index("last")] == 0

    def without_names(self, pattern: tuple) -> tuple:
        names = self.schema.field_names
        out = list(pattern)
        out[names.index("first")] = out[names.index("last")] = None
        return tuple(out)

    def decide(self, w: float, pattern: tuple) -> tuple[str, bool]:
        """(decision, guard_ok). guard_ok=False means a guard prevents auto-merge however high the weight."""
        guard_ok = True
        if self._name_missing(pattern):
            if self.name_missing_policy == "review" or (
                self.name_missing_policy == "phone+email" and not self._phone_and_email_agree(pattern)
            ):
                guard_ok = False
        if self.conflict_veto and self._conflict(pattern):
            guard_ok = False
        if w >= self.t_merge:
            return (MERGE if guard_ok else REVIEW), guard_ok
        return (REVIEW if w >= self.t_review else NON_MATCH), guard_ok

    def _name_missing(self, pattern: tuple) -> bool:
        names = self.schema.field_names
        return pattern[names.index("first")] is None or pattern[names.index("last")] is None

    def _phone_and_email_agree(self, pattern: tuple) -> bool:
        names = self.schema.field_names
        if "email" not in names:  # benchmark schema without contact details: no such guard possible
            return False
        return pattern[names.index("phone")] == 2 and pattern[names.index("email")] == 2

    @timed
    def match(self, clean: pd.DataFrame) -> MatchOutput:
        feats, rows, stats = candidate_patterns(self.schema, clean)
        if not rows:
            return MatchOutput(empty_pairs(), 0, 0.0)
        fit_rows = [r for r in rows if not (self.conflict_veto and self._conflict(r[3]))]
        self.fit(feats, fit_rows)
        table = self.weight_table()
        out = []
        for i, j, _, pat in rows:
            conflict = self.conflict_veto and self._conflict(pat)
            w = self.weight(self.without_names(pat) if conflict else pat, table)
            a, b = ordered(feats, i, j)
            decision, guard_ok = self.decide(w, pat)
            out.append((a, b, w, decision, evidence_string(self.schema, pat), guard_ok))
        pairs = pd.DataFrame(out, columns=PAIR_COLUMNS)
        info = {
            "blocking": {
                "reduction_ratio": stats.reduction_ratio,
                "per_rule_pairs": stats.per_rule_pairs,
                "skipped_blocks": stats.per_rule_skipped_blocks,
            },
            "parameters": self.parameters(),
        }
        return MatchOutput(pairs, len(pairs), 0.0, info)


def log2_odds_to_probability(w: float, prior: float = 0.001) -> float:
    """Convert a prior-free match weight into a probability for a stated prior (illustration only)."""
    odds = math.pow(2, w) * prior / (1 - prior)
    return odds / (1 + odds)
