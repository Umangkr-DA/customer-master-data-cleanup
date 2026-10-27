"""Incremental matching: add new records to an existing set of golden records without rebuilding everything.

How it works
  bootstrap  : run the Fellegi-Sunter matcher once on the existing records. That fits m/u (EM) and decides pairs. The
               fitted weight table is then FROZEN, and per-rule block indexes (key -> record indexes) are kept.
  add_records: featurize only the new records, append them to the block indexes, generate candidates only from blocks
               that received a new record (new x existing and new x new), score them with the frozen weight table and
               the matcher's own decide() (same guards, same thresholds), union the merge pairs into the clusters, and
               rebuild survivorship only for the clusters that changed.

What is guaranteed (and tested in tests/test_incremental.py)
  With the weight table held fixed, the set of scored pairs and merge decisions after any sequence of add_records()
  calls equals a full rebuild over all records with the same frozen weights (rebuild_frozen), pair for pair, and the
  clusters and golden-record field values are identical. Candidate generation, scoring and clustering are order
  independent, so batch size and batch order do not matter.

What is NOT guaranteed (reported, not hidden)
  1. A full rebuild RE-ESTIMATES m/u on all records; incremental keeps the weights from the bootstrap. Small decision
     differences near the thresholds are expected; scripts/run_incremental.py measures them.
  2. Blocks larger than rule.max_block are skipped. If a block grows past the cap, a full rebuild would drop ALL of its
     pairs, but incremental cannot un-merge pairs accepted while the block was small. Such events are counted in
     IncrementalResult.cap_crossed_blocks. New pairs from an over-cap block are skipped, as in a rebuild.
  3. golden_id numbers (a dense rank over cluster labels) are not stable under growth; the stable key here is
     cluster_label (the smallest record_id in the cluster). A production system would mint surrogate keys.
  4. Merges are permanent. A later batch can add evidence that two clusters should never have merged; only a rebuild
     (or a review workflow) can split them.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

import pandas as pd

from customer_master.clustering import UnionFind
from customer_master.matchers.base import MERGE, PAIR_COLUMNS
from customer_master.matchers.common import candidate_patterns, evidence_string, ordered
from customer_master.schema import CUSTOMER, Schema
from customer_master.survivorship import build_golden


def score_candidates(matcher, schema: Schema, feats: list, rows: list) -> pd.DataFrame:
    """Score (i, j, mask, pattern) rows with the matcher's CURRENT weight table (no re-fit). Same arithmetic as
    FellegiSunterMatcher.match()."""
    table = matcher.weight_table()
    out = []
    for i, j, _, pat in rows:
        conflict = matcher.conflict_veto and matcher._conflict(pat)
        w = matcher.weight(matcher.without_names(pat) if conflict else pat, table)
        a, b = ordered(feats, i, j)
        decision, guard_ok = matcher.decide(w, pat)
        out.append((a, b, w, decision, evidence_string(schema, pat), guard_ok))
    return pd.DataFrame(out, columns=PAIR_COLUMNS)


def rebuild_frozen(clean: pd.DataFrame, matcher, schema: Schema = CUSTOMER) -> pd.DataFrame:
    """Reference for the equality tests: full candidate generation over ALL records, scored with frozen weights."""
    feats, rows, _ = candidate_patterns(schema, clean.reset_index(drop=True))
    return score_candidates(matcher, schema, feats, rows).sort_values(["a_id", "b_id"]).reset_index(drop=True)


@dataclass
class IncrementalResult:
    new_pairs: pd.DataFrame  # every scored pair that involves at least one new record
    merged_pairs: list  # the subset decided MERGE
    touched_labels: set  # cluster labels (after this batch) whose membership changed or that contain a new record
    retired_labels: set  # labels that existed before and were absorbed into another cluster
    cap_crossed_blocks: int  # blocks that grew past max_block in this batch (see module docstring, item 2)
    runtime_s: float
    golden: pd.DataFrame = field(default_factory=pd.DataFrame)  # golden records of the touched clusters


class IncrementalMatcher:
    def __init__(self, matcher, schema: Schema = CUSTOMER):
        self.matcher, self.schema = matcher, schema
        self.clean = pd.DataFrame()
        self.feats: list = []
        self.indexes: list = [defaultdict(list) for _ in schema.block_rules]
        self.uf = UnionFind()
        self.members: dict = {}  # cluster label -> record ids
        self.merged_pairs: list = []
        self.review_pairs = pd.DataFrame(columns=PAIR_COLUMNS)

    # ------------------------------------------------------------------ build
    @classmethod
    def bootstrap(cls, clean: pd.DataFrame, matcher, schema: Schema = CUSTOMER) -> "IncrementalMatcher":
        """Full match on the existing records (fits and freezes the weight table), then index them for updates."""
        clean = clean.reset_index(drop=True)
        out = matcher.match(clean)
        inc = cls(matcher, schema)
        inc.clean = clean
        inc.feats = schema.featurize(clean)
        for idx, f in enumerate(inc.feats):
            inc._index(idx, f)
        for rid in clean.record_id:
            inc.uf.parent[rid] = rid
        pairs = out.pairs
        merged = pairs[pairs.decision == MERGE]
        inc.merged_pairs = list(zip(merged.a_id, merged.b_id))
        for a, b in inc.merged_pairs:
            inc.uf.union(a, b)
        inc.review_pairs = pairs[pairs.decision == "review"].reset_index(drop=True)
        inc.members = _group(inc.uf, clean.record_id)
        return inc

    def _index(self, idx: int, f: dict) -> list:
        touched = []
        for r_idx, rule in enumerate(self.schema.block_rules):
            for key in set(rule.keys(f)):
                self.indexes[r_idx][key].append(idx)
                touched.append((r_idx, key))
        return touched

    # ------------------------------------------------------------------ update
    def add_records(self, new_clean: pd.DataFrame, with_golden: bool = True) -> IncrementalResult:
        t0 = time.perf_counter()
        new_clean = new_clean.reset_index(drop=True)
        clash = set(new_clean.record_id) & set(self.uf.parent)
        if clash:
            raise ValueError(f"record ids already present: {sorted(clash)[:3]}")
        n_old = len(self.feats)
        new_feats = self.schema.featurize(new_clean)
        self.feats.extend(new_feats)
        self.clean = pd.concat([self.clean, new_clean], ignore_index=True)

        touched_keys: set = set()
        for k, f in enumerate(new_feats):
            touched_keys.update(self._index(n_old + k, f))

        rules = self.schema.block_rules
        cand: dict = defaultdict(int)
        crossed = 0
        for r_idx, key in touched_keys:
            members = self.indexes[r_idx][key]
            if len(members) < 2:
                continue
            if len(members) > rules[r_idx].max_block:
                n_new = sum(1 for m in members if m >= n_old)
                if len(members) - n_new <= rules[r_idx].max_block:
                    crossed += 1
                continue
            for m in members:
                if m < n_old:
                    continue
                for o in members:
                    if o < m:
                        cand[(o, m)] |= 1 << r_idx
                    else:
                        break  # members is in ascending index order
        compare = self.schema.compare
        rows = [(i, j, mask, compare(self.feats[i], self.feats[j])) for (i, j), mask in cand.items()]
        new_pairs = score_candidates(self.matcher, self.schema, self.feats, rows)
        new_pairs = new_pairs.sort_values(["a_id", "b_id"]).reset_index(drop=True)

        merged = new_pairs[new_pairs.decision == MERGE]
        merged_list = list(zip(merged.a_id, merged.b_id))
        new_ids = list(new_clean.record_id)
        old_ids_in_merges = {x for pair in merged_list for x in pair} - set(new_ids)
        old_labels = {self.uf.find(x) for x in old_ids_in_merges}
        for rid in new_ids:
            self.uf.parent[rid] = rid
        for a, b in merged_list:
            self.uf.union(a, b)
        self.merged_pairs.extend(merged_list)
        review = new_pairs[new_pairs.decision == "review"]
        if len(review):
            self.review_pairs = pd.concat([self.review_pairs, review], ignore_index=True)

        retired = {lab for lab in old_labels if self.uf.find(lab) != lab}
        affected_ids = list(new_ids)
        for lab in old_labels:
            affected_ids.extend(self.members.pop(lab))
        regrouped = _group(self.uf, affected_ids)
        self.members.update(regrouped)
        touched = set(regrouped)
        result = IncrementalResult(new_pairs, merged_list, touched, retired, crossed, 0.0)
        if with_golden:
            result.golden = self.golden_records(touched)
        result.runtime_s = time.perf_counter() - t0
        return result

    # ------------------------------------------------------------------ outputs
    def clusters(self) -> dict:
        """record_id -> cluster label (smallest record_id in the cluster)."""
        return {rid: self.uf.find(rid) for rid in self.uf.parent}

    def golden_records(self, labels=None) -> pd.DataFrame:
        """Survivorship (the unchanged sql/04 rules) for the given clusters only (default: all)."""
        labels = set(self.members) if labels is None else set(labels)
        ids = {rid for lab in labels for rid in self.members[lab]}
        if not ids:
            return pd.DataFrame()
        sub = self.clean[self.clean.record_id.isin(ids)].reset_index(drop=True)
        pairs = [(a, b) for a, b in self.merged_pairs if a in ids]
        golden, cmap = build_golden(sub, pairs)
        label_of_golden = cmap.groupby("golden_id").record_id.min()
        golden = golden.copy()
        golden.insert(0, "cluster_label", golden.golden_id.map(label_of_golden))
        return golden.sort_values("cluster_label").reset_index(drop=True)


def _group(uf: UnionFind, record_ids) -> dict:
    groups: dict = defaultdict(list)
    for rid in record_ids:
        groups[uf.find(rid)].append(rid)
    return dict(groups)
