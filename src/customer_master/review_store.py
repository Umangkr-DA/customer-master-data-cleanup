"""Human-in-the-loop review decisions, stored in SQLite as an append-only audit trail.

A reviewer looks at one candidate pair from the review queue (outputs/review_queue.csv) and records one of three
verdicts:

  merge                  treat the two records as the same customer
  keep_separate          preserve both records as different customers
  needs_investigation    defer; the pair stays in the pending list

Design rules (data governance):

  * Append-only. A decision is never updated or deleted (SQL triggers enforce it). Changing your mind means adding a
    newer row; the older row stays visible in the audit trail. The "current" verdict of a pair is its latest row.
  * Every row keeps the pair's record ids, the matcher's score and match probability at decision time, the reviewer,
    a mandatory free-text reason, and a UTC timestamp.
  * Separate from evaluation. This module never reads an answer key and never writes to outputs/ or data/. Reviewer
    verdicts are operational data, not ground truth: they must not be used to tune or score the matchers, otherwise
    the held-out evaluation would no longer be held out. tests/test_no_leakage.py guards the answer-key import rule.
  * Recording a verdict does not change the golden records. `approved_merges()` / `approved_separations()` expose the
    verdicts so a downstream step can apply them deliberately.
"""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
from pathlib import Path

import pandas as pd

from customer_master.common import PROJECT_ROOT
from customer_master.review import parse_levels

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "processed" / "review_decisions.db"  # git-ignored runtime state
DB_ENV_VAR = "CUSTOMER_MDM_REVIEW_DB"  # optional override, e.g. a persistent volume on a hosted deployment

MERGE = "merge"
KEEP_SEPARATE = "keep_separate"
NEEDS_INVESTIGATION = "needs_investigation"
DECISIONS = (MERGE, KEEP_SEPARATE, NEEDS_INVESTIGATION)
FINAL_DECISIONS = (MERGE, KEEP_SEPARATE)  # a pair with one of these is no longer pending

_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_decisions (
    decision_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    a_id               TEXT NOT NULL,
    b_id               TEXT NOT NULL,
    decision           TEXT NOT NULL CHECK (decision IN ('merge', 'keep_separate', 'needs_investigation')),
    reason             TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    reviewer           TEXT NOT NULL CHECK (length(trim(reviewer)) > 0),
    match_score        REAL,
    match_probability  REAL,
    queue_rank         INTEGER,
    decided_at_utc     TEXT NOT NULL,
    CHECK (a_id < b_id)
);
CREATE INDEX IF NOT EXISTS ix_review_pair ON review_decisions (a_id, b_id);
CREATE TRIGGER IF NOT EXISTS review_decisions_no_update
    BEFORE UPDATE ON review_decisions BEGIN SELECT RAISE(ABORT, 'review_decisions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS review_decisions_no_delete
    BEFORE DELETE ON review_decisions BEGIN SELECT RAISE(ABORT, 'review_decisions is append-only'); END;
"""

_COLUMNS = [
    "decision_id", "a_id", "b_id", "decision", "reason", "reviewer", "match_score", "match_probability",
    "queue_rank", "decided_at_utc",
]  # fmt: skip


def pair_key(a_id: str, b_id: str) -> tuple:
    """Order-independent pair identity: (A, B) and (B, A) are the same pair."""
    a, b = str(a_id), str(b_id)
    if a == b:
        raise ValueError("a pair needs two different record ids")
    return (a, b) if a < b else (b, a)


def connect(path=None) -> sqlite3.Connection:
    """Open (and create if needed) the decision database: `path`, else $CUSTOMER_MDM_REVIEW_DB, else the default."""
    path = path or os.environ.get(DB_ENV_VAR) or DEFAULT_DB_PATH
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.executescript(_SCHEMA)
    return con


def record_decision(
    con: sqlite3.Connection,
    a_id: str,
    b_id: str,
    decision: str,
    reason: str,
    reviewer: str,
    match_score: float | None = None,
    match_probability: float | None = None,
    queue_rank: int | None = None,
    now: dt.datetime | None = None,
) -> int:
    """Append one verdict and return its decision_id. Raises ValueError on a bad decision or an empty reason."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}, got {decision!r}")
    if not str(reason or "").strip():
        raise ValueError("a reason is required for every decision")
    if not str(reviewer or "").strip():
        raise ValueError("a reviewer name is required for every decision")
    a, b = pair_key(a_id, b_id)
    stamp = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).isoformat(timespec="seconds")
    cur = con.execute(
        "INSERT INTO review_decisions (a_id, b_id, decision, reason, reviewer, match_score, match_probability, "
        "queue_rank, decided_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            a, b, decision, str(reason).strip(), str(reviewer).strip(),
            None if match_score is None else float(match_score),
            None if match_probability is None else float(match_probability),
            None if queue_rank is None else int(queue_rank),
            stamp,
        ),
    )  # fmt: skip
    con.commit()
    return int(cur.lastrowid)


def audit_trail(con: sqlite3.Connection, a_id: str | None = None, b_id: str | None = None) -> pd.DataFrame:
    """Every verdict ever recorded, oldest first; optionally only those for one pair."""
    sql, params = f"SELECT {', '.join(_COLUMNS)} FROM review_decisions", ()
    if a_id is not None and b_id is not None:
        sql += " WHERE a_id = ? AND b_id = ?"
        params = pair_key(a_id, b_id)
    return pd.read_sql_query(sql + " ORDER BY decision_id", con, params=params)


def current_decisions(con: sqlite3.Connection) -> pd.DataFrame:
    """The latest verdict of every reviewed pair."""
    trail = audit_trail(con)
    if trail.empty:
        return trail
    return trail.sort_values("decision_id").groupby(["a_id", "b_id"], as_index=False).tail(1).reset_index(drop=True)


def approved_merges(con: sqlite3.Connection) -> pd.DataFrame:
    cur = current_decisions(con)
    return cur[cur.decision == MERGE].reset_index(drop=True)


def approved_separations(con: sqlite3.Connection) -> pd.DataFrame:
    cur = current_decisions(con)
    return cur[cur.decision == KEEP_SEPARATE].reset_index(drop=True)


def pending_queue(queue: pd.DataFrame, con: sqlite3.Connection) -> pd.DataFrame:
    """Queue rows whose pair has no final verdict yet (never reviewed, or only 'needs_investigation')."""
    if queue.empty:
        return queue
    cur = current_decisions(con)
    done = set()
    if not cur.empty:
        done = {(a, b) for a, b, d in zip(cur.a_id, cur.b_id, cur.decision) if d in FINAL_DECISIONS}
    keep = [pair_key(a, b) not in done for a, b in zip(queue.a_id, queue.b_id)]
    return queue[keep].reset_index(drop=True)


def decision_summary(con: sqlite3.Connection) -> dict:
    cur = current_decisions(con)
    counts = {d: int((cur.decision == d).sum()) if not cur.empty else 0 for d in DECISIONS}
    return {"pairs_reviewed": len(cur), "total_actions": len(audit_trail(con)), **counts}


# ----------------------------------------------------------------------------- explaining a pair
_FIELD_GROUPS = {
    "full_name": ("first", "last"),
    "phone": ("phone",),
    "email": ("email",),
    "city": ("city",),
    "pincode": ("pincode",),
    "signup_date": ("date",),
}
_LEVEL_TEXT = {2: "exact match", 1: "similar (partial agreement)", 0: "different"}


def explain_pair(row: pd.Series) -> pd.DataFrame:
    """Field-by-field evidence for one review-queue row: does each field support, contradict or say nothing?

    Uses only what is in the queue row (levels written by the matcher), so it explains the matcher's own view.
    """
    levels = parse_levels(str(row["levels"]))
    out = []
    for field, parts in _FIELD_GROUPS.items():
        lv = [levels.get(p) for p in parts]
        known = [x for x in lv if x is not None]
        a_val, b_val = row.get(f"a_{field}"), row.get(f"b_{field}")
        if not known:
            verdict, why = "missing", "at least one record has no usable value"
        elif min(known) == 0:
            verdict, why = "contradicts", _LEVEL_TEXT[0]
        elif min(known) >= 2:
            verdict, why = "supports", _LEVEL_TEXT[2]
        else:
            verdict, why = "supports", _LEVEL_TEXT[1]
        out.append(
            {
                "field": field,
                "record_a": "" if pd.isna(a_val) else a_val,
                "record_b": "" if pd.isna(b_val) else b_val,
                "evidence": verdict,
                "detail": why,
            }
        )
    return pd.DataFrame(out)


def risk_note(explanation: pd.DataFrame) -> str:
    """One plain-language sentence for the reviewer about what a wrong 'merge' would risk."""
    n_for = int((explanation.evidence == "supports").sum())
    n_against = int((explanation.evidence == "contradicts").sum())
    n_missing = int((explanation.evidence == "missing").sum())
    if n_against:
        return (
            f"{n_against} field(s) contradict a match while {n_for} support it. Merging two different customers "
            "mixes their contact details and history; prefer 'keep separate' or 'needs investigation' unless you "
            "can explain the contradiction (e.g. a typo)."
        )
    if n_missing:
        return (
            f"No field contradicts the match, but {n_missing} field(s) are missing, so the evidence is incomplete. "
            "Shared family phones/emails are the usual cause of a wrong merge here."
        )
    return "Every field supports a match; a merge is low risk."
