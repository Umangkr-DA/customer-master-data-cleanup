"""Matcher (b): deterministic fuzzy rules.

Same philosophy as the exact-key baseline (explainable, no model) but tolerant of the noise that exact keys miss:
Jaro-Winkler / phonetic / initial / swapped-order names, a one-digit phone typo, an email username that is the same
apart from digits, and phonetic-name blocking. Each rule tier gives the pair a score; merge and review thresholds
are chosen on DEV and frozen.

Tiers (a name "agrees" when both name-token levels are >= 1 and their sum >= 4):
  1.00  phone exact AND email exact (names agree or a name is missing)
  0.90  phone exact OR email exact, names agree with both tokens exact/abbreviated (levels >= 2)
  0.80  phone exact OR email exact, names agree (a typo-level token allowed)
  0.65  one identifier typo (phone 1-edit or same email username) AND names agree AND (pincode or city agrees)
  0.55  names agree with both tokens exact AND pincode agrees AND city agrees, no identifier evidence
  0.00  anything else
Name unknown (blank): only tier 1.00 applies. Names that disagree (a level 0 token): score 0, which keeps relatives
and recycled-phone owners apart, as the baseline's name guard does.
"""

from __future__ import annotations

import pandas as pd

from customer_master.matchers.base import MERGE, NON_MATCH, PAIR_COLUMNS, REVIEW, MatchOutput, empty_pairs, timed
from customer_master.matchers.common import SHARED_CODE, candidate_patterns, code_fingerprint, evidence_string, ordered
from customer_master.schema import CUSTOMER, Schema


class FuzzyRuleMatcher:
    name = "fuzzy-rules"

    def __init__(self, schema: Schema = CUSTOMER, t_merge: float = 0.8, t_review: float = 0.55):
        self.schema, self.t_merge, self.t_review = schema, t_merge, t_review
        self._ix = {n: i for i, n in enumerate(schema.field_names)}

    def config(self) -> dict:
        return {
            "matcher": self.name, "schema": self.schema.name, "t_merge": self.t_merge, "t_review": self.t_review,
            "block_rules": [(r.name, sorted(r.fields), r.max_block) for r in self.schema.block_rules],
            "code_sha": code_fingerprint(*SHARED_CODE, "matchers/fuzzy.py"),
        }  # fmt: skip

    def score(self, p: tuple) -> float:
        g = lambda name: p[self._ix[name]]  # noqa: E731
        first, last = g("first"), g("last")
        if "email" not in self._ix:
            return self._score_benchmark(p)
        phone, email, pin, city = g("phone"), g("email"), g("pincode"), g("city")
        if first is None or last is None:
            return 1.0 if phone == 2 and email == 2 else 0.0
        if first == 0 or last == 0 or first + last < 4:
            return 0.0
        strong_id = phone == 2 or email == 2
        if phone == 2 and email == 2:
            return 1.0
        if strong_id and first >= 2 and last >= 2:
            return 0.9
        if strong_id:
            return 0.8
        if (phone == 1 or email == 1) and (pin == 1 or city == 1):
            return 0.65
        if first == 3 and last == 3 and pin == 1 and city == 1:
            return 0.55
        return 0.0

    def _score_benchmark(self, p: tuple) -> float:
        """Febrl has no phone/email: identifiers are social-security id and date of birth (exact or one typo)."""
        g = lambda name: p[self._ix[name]]  # noqa: E731
        first, last, ssid, dob = g("first"), g("last"), g("soc_sec_id"), g("dob")
        if first is None or last is None:
            return 1.0 if ssid == 2 and dob == 2 else 0.0
        if first == 0 or last == 0 or first + last < 4:
            return 0.0
        if ssid == 2 and dob == 2:
            return 1.0
        if ssid == 2 or dob == 2:
            return 0.9 if first >= 2 and last >= 2 else 0.8
        if (ssid == 1 or dob == 1) and (g("postcode") in (1, 2) or g("suburb") in (1, 2)):
            return 0.65
        if first == 3 and last == 3 and g("postcode") == 2 and g("suburb") == 2:
            return 0.55
        return 0.0

    @timed
    def match(self, clean: pd.DataFrame) -> MatchOutput:
        feats, rows, stats = candidate_patterns(self.schema, clean)
        if not rows:
            return MatchOutput(empty_pairs(), 0, 0.0)
        out = []
        for i, j, _, pat in rows:
            s = self.score(pat)
            a, b = ordered(feats, i, j)
            decision = MERGE if s >= self.t_merge else REVIEW if s >= self.t_review else NON_MATCH
            out.append((a, b, s, decision, evidence_string(self.schema, pat), True))
        pairs = pd.DataFrame(out, columns=PAIR_COLUMNS)
        info = {"blocking": {"reduction_ratio": stats.reduction_ratio, "per_rule_pairs": stats.per_rule_pairs}}
        return MatchOutput(pairs, len(pairs), 0.0, info)
