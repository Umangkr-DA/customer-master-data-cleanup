"""Matcher (a): the original exact-key rules. Runs the REAL SQL (sql/02 + sql/03) on an in-memory SQLite copy of
the standardized records, so the baseline is the shipped logic, not a re-implementation."""

from __future__ import annotations

import hashlib
import sqlite3

import pandas as pd

from customer_master.common import CITY_PIN_PREFIX, SQL_DIR, name_match
from customer_master.matchers.base import MERGE, NON_MATCH, MatchOutput, empty_pairs, timed


class ExactKeyMatcher:
    name = "exact-key"

    def config(self) -> dict:
        sql = "".join(
            (SQL_DIR / f).read_text(encoding="utf-8") for f in ("02_duplicate_detection.sql", "03_matching.sql")
        )
        return {"matcher": self.name, "sql_sha256": hashlib.sha256(sql.encode()).hexdigest()[:16]}

    @timed
    def match(self, clean: pd.DataFrame) -> MatchOutput:
        con = sqlite3.connect(":memory:")
        con.create_function("name_match", 2, name_match)
        clean.to_sql("clean_customers", con, index=False)
        con.execute("CREATE TABLE city_ref (city TEXT PRIMARY KEY, pin_prefix TEXT)")
        con.executemany("INSERT INTO city_ref VALUES (?, ?)", CITY_PIN_PREFIX.items())
        for f in ("02_duplicate_detection.sql", "03_matching.sql"):
            con.executescript((SQL_DIR / f).read_text(encoding="utf-8"))
        scored = pd.read_sql(
            "SELECT s.a_id, s.b_id, s.keys_agreeing AS score, "
            "CASE WHEN e.a_id IS NULL THEN 0 ELSE 1 END AS accepted, "
            "s.phone_match, s.email_match, s.name_pin_match, s.name_status "
            "FROM scored_pairs s LEFT JOIN match_edges e ON e.a_id = s.a_id AND e.b_id = s.b_id",
            con,
        )
        con.close()
        if scored.empty:
            return MatchOutput(empty_pairs(), 0, 0.0)
        scored["decision"] = scored.accepted.map({1: MERGE, 0: NON_MATCH})
        scored["evidence"] = [
            f"phone={p} email={e} name+pin={n} name_status={s}"
            for p, e, n, s in zip(scored.phone_match, scored.email_match, scored.name_pin_match, scored.name_status)
        ]
        scored["guard_ok"] = True
        pairs = scored[["a_id", "b_id", "score", "decision", "evidence", "guard_ok"]].reset_index(drop=True)
        return MatchOutput(pairs, len(pairs), 0.0)
