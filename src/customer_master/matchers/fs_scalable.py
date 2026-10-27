"""Memory-lean Fellegi-Sunter matcher for large inputs. Same model and same decisions as FellegiSunterMatcher.

Why it exists: the reference matcher keeps one Python tuple per candidate pair (indexes, mask, comparison pattern) and,
after scoring, one more tuple plus an evidence string per pair. That is fine at 5k records but ran out of memory at 500k
(see outputs/scale_benchmark.csv). Three changes remove the hot spots without touching the statistics:

  1. Pairs are held as int64 codes (i * n + j) and a uint32 pattern id; there are only a few thousand
     distinct comparison patterns, so a pair costs 12 bytes instead of several hundred. Duplicate pairs across
     blocking rules are removed with sorted-array set operations rather than a dict of tuples.
  2. EM needs only pattern COUNTS, and scoring is a pure function of the pattern, so both run once per distinct
     pattern, not once per pair. Evidence strings are built per pattern and only for pairs that are kept.
  3. Name-token comparison (Jaro-Winkler, phonetic key, edit distance) is a pure function of two short strings
     drawn from a small vocabulary, so it is memoised for the duration of one match() call.

Only MERGE and REVIEW pairs are returned (a non-match carries no information that a caller can use); n_candidates
still counts every candidate. Only em_mode="joint" is supported (the split mode needs per-rule masks, which this
layout drops).

tests/test_fs_scalable.py checks that decisions and scores equal the reference matcher on generated data.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from contextlib import contextmanager
from functools import lru_cache
from itertools import combinations

import numpy as np
import pandas as pd

import customer_master.schema as schema_module
from customer_master.blocking import BlockingStats
from customer_master.matchers.base import MERGE, NON_MATCH, PAIR_COLUMNS, MatchOutput, empty_pairs, timed
from customer_master.matchers.common import code_fingerprint, evidence_string
from customer_master.matchers.fellegi_sunter import FellegiSunterMatcher

_CHUNK = 500_000
_SMALL_BLOCK = 12


@contextmanager
def cached_token_levels():
    """Memoise schema.token_level (pure function of two strings) while a match runs, then restore it."""
    original = schema_module.token_level
    schema_module.token_level = lru_cache(maxsize=2_000_000)(original)
    try:
        yield
    finally:
        schema_module.token_level = original


class ScalableFellegiSunter(FellegiSunterMatcher):
    name = "fellegi-sunter-scalable"

    def __init__(self, *args, prior_correction: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        if self.em_mode != "joint":
            raise ValueError("ScalableFellegiSunter supports em_mode='joint' only")
        self.freeze_weights = False  # True after fit_on(): match() then scores with the stored m/u and skips EM
        # Prior-odds correction (opt-in, needs fit_on). A record's true duplicate partners stay constant as a file
        # grows, but its non-match candidates grow with N, so the prior odds of a candidate pair fall as 1/N. Both
        # thresholds are therefore raised by log2(N / N_fit) (never lowered). Derived from that argument, not tuned.
        self.prior_correction = prior_correction
        self.n_reference = None  # records in the extract the frozen weights were fitted on

    def fit_on(self, reference: pd.DataFrame) -> "ScalableFellegiSunter":
        """Estimate m/u on a representative extract, then freeze them for every later match() call.

        Why: EM on the full candidate set is unstable as a file grows (see outputs/scale_benchmark.csv: at 500k records
        the latent class stops meaning "same customer"). Fitting once on a bounded extract and scoring the large file
        with frozen weights is also how incremental matching works.
        """
        self.freeze_weights = False
        self.match(reference)
        self.freeze_weights = True
        self.n_reference = len(reference)
        return self

    def threshold_shift(self, n_records: int) -> float:
        """Bits added to t_merge and t_review for a file of n_records (0 unless prior_correction is on and fitted)."""
        if not self.prior_correction:
            return 0.0
        if not self.freeze_weights or not self.n_reference:
            raise ValueError("prior_correction needs weights frozen with fit_on() (it scales from the extract size)")
        return max(0.0, math.log2(n_records / self.n_reference))

    def config(self) -> dict:
        cfg = super().config()
        cfg["matcher"] = self.name
        cfg["code_sha_scalable"] = code_fingerprint("matchers/fs_scalable.py")
        cfg["freeze_weights"] = self.freeze_weights
        cfg["prior_correction"] = self.prior_correction
        cfg["n_reference"] = self.n_reference
        return cfg

    # ------------------------------------------------------------------ candidates
    def _candidates(self, feats: list, stage: dict):
        """Blocking + comparison. Returns (codes, pattern ids, distinct patterns, BlockingStats)."""
        n, schema = len(feats), self.schema
        compare = schema.compare
        stats = BlockingStats(n)
        seen = np.empty(0, dtype=np.int64)
        code_chunks: list = []
        pid_chunks: list = []
        pat_index: dict = {}
        patterns: list = []
        t_block = t_compare = 0.0
        for rule in schema.block_rules:
            t0 = time.perf_counter()
            blocks: dict = defaultdict(list)
            for i, f in enumerate(feats):
                for key in set(rule.keys(f)):
                    blocks[key].append(i)
            parts, small, skipped_blocks, skipped_records = [], [], 0, 0
            for members in blocks.values():
                k = len(members)
                if k < 2:
                    continue
                if k > rule.max_block:
                    skipped_blocks += 1
                    skipped_records += k
                    continue
                if k <= _SMALL_BLOCK:  # numpy call overhead dominates for the many tiny blocks
                    small.extend(x * n + y for x, y in combinations(members, 2))  # members ascending, so x < y
                    continue
                arr = np.asarray(members, dtype=np.int64)
                a, b = np.triu_indices(k, 1)
                parts.append(arr[a] * n + arr[b])
            if small:
                parts.append(np.asarray(small, dtype=np.int64))
            del blocks, small
            stats.per_rule_skipped_blocks[rule.name] = skipped_blocks
            stats.per_rule_skipped_records[rule.name] = skipped_records
            rule_codes = np.unique(np.concatenate(parts)) if parts else np.empty(0, dtype=np.int64)
            del parts
            stats.per_rule_pairs[rule.name] = int(len(rule_codes))
            fresh = np.setdiff1d(rule_codes, seen, assume_unique=True)
            seen = np.union1d(seen, fresh)
            del rule_codes
            t_block += time.perf_counter() - t0

            t0 = time.perf_counter()
            pids = np.empty(len(fresh), dtype=np.uint32)
            for s in range(0, len(fresh), _CHUNK):
                part = fresh[s : s + _CHUNK]
                out = []
                for i, j in zip((part // n).tolist(), (part % n).tolist()):
                    pat = compare(feats[i], feats[j])
                    p = pat_index.get(pat)
                    if p is None:
                        p = pat_index[pat] = len(patterns)
                        patterns.append(pat)
                    out.append(p)
                pids[s : s + len(out)] = out
            code_chunks.append(fresh)
            pid_chunks.append(pids)
            t_compare += time.perf_counter() - t0
        del seen
        codes = np.concatenate(code_chunks) if code_chunks else np.empty(0, dtype=np.int64)
        pid_all = np.concatenate(pid_chunks) if pid_chunks else np.empty(0, dtype=np.uint32)
        stats.n_pairs = int(len(codes))
        stage["block_s"], stage["compare_s"] = round(t_block, 2), round(t_compare, 2)
        return codes, pid_all, patterns, stats

    # ------------------------------------------------------------------ match
    @timed
    def match(self, clean: pd.DataFrame) -> MatchOutput:
        stage: dict = {}
        with cached_token_levels():
            t0 = time.perf_counter()
            feats = self.schema.featurize(clean)
            stage["featurize_s"] = round(time.perf_counter() - t0, 2)
            codes, pid_all, patterns, stats = self._candidates(feats, stage)
            if len(codes) == 0:
                return MatchOutput(empty_pairs(), 0, 0.0)

            t0 = time.perf_counter()
            veto = self.conflict_veto
            conflict = np.array([bool(veto and self._conflict(p)) for p in patterns])
            if self.freeze_weights:
                if not self.m:
                    raise ValueError("freeze_weights is set but no weights were fitted; call fit_on() first")
            else:
                counts = np.bincount(pid_all, minlength=len(patterns)).astype(float)
                fit_mask = (~conflict) & (counts > 0)
                enc = np.array([[-1 if x is None else x for x in p] for p in patterns], dtype=int)
                u0 = self.estimate_u(feats)
                self.m, self.u = self._em_joint(enc[fit_mask], counts[fit_mask], u0)
            stage["fit_em_s"] = round(time.perf_counter() - t0, 2)

        t0 = time.perf_counter()
        table = self.weight_table()
        n_pat = len(patterns)
        weight = np.empty(n_pat)
        decision = np.empty(n_pat, dtype=object)
        guard = np.empty(n_pat, dtype=bool)
        evidence = [evidence_string(self.schema, p) for p in patterns]
        shift = self.threshold_shift(len(clean)) if self.freeze_weights else 0.0  # no shift while fitting
        t_merge0, t_review0 = self.t_merge, self.t_review
        self.t_merge, self.t_review = t_merge0 + shift, t_review0 + shift
        try:
            for k, pat in enumerate(patterns):
                weight[k] = self.weight(self.without_names(pat) if conflict[k] else pat, table)
                decision[k], guard[k] = self.decide(weight[k], pat)
        finally:
            self.t_merge, self.t_review = t_merge0, t_review0
        keep_pat = decision != NON_MATCH
        idx = np.nonzero(keep_pat[pid_all])[0]
        n = len(feats)
        ids = np.array([f["id"] for f in feats], dtype=object)
        ci, cj = codes[idx] // n, codes[idx] % n
        id_i, id_j = ids[ci], ids[cj]
        swap = id_i > id_j
        a_id = np.where(swap, id_j, id_i)
        b_id = np.where(swap, id_i, id_j)
        pid_kept = pid_all[idx]
        pairs = pd.DataFrame(
            {
                "a_id": a_id,
                "b_id": b_id,
                "score": weight[pid_kept],
                "decision": decision[pid_kept],
                "evidence": np.array(evidence, dtype=object)[pid_kept],
                "guard_ok": guard[pid_kept],
            },
            columns=PAIR_COLUMNS,
        )
        pairs = pairs.sort_values(["a_id", "b_id"]).reset_index(drop=True)
        stage["score_s"] = round(time.perf_counter() - t0, 2)
        info = {
            "blocking": {
                "reduction_ratio": stats.reduction_ratio,
                "per_rule_pairs": stats.per_rule_pairs,
                "skipped_blocks": stats.per_rule_skipped_blocks,
                "skipped_records": stats.per_rule_skipped_records,
            },
            "parameters": self.parameters(),
            "stage_s": stage,
            "distinct_patterns": n_pat,
            "returned_pairs": "merge and review only",
            "threshold_shift_bits": round(shift, 3),
            "merge_pairs": int((pairs.decision == MERGE).sum()),
        }
        return MatchOutput(pairs, int(len(codes)), 0.0, info)
