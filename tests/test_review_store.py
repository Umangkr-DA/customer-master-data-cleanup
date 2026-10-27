import sqlite3

import pandas as pd
import pytest

from customer_master import review_store as rs


@pytest.fixture
def con():
    c = rs.connect(":memory:")
    yield c
    c.close()


def _decide(con, a="CRM-1", b="WEB-2", decision=rs.MERGE, reason="same phone and e-mail", **kw):
    return rs.record_decision(con, a, b, decision, reason, kw.pop("reviewer", "asha"), **kw)


def test_decision_is_stored_with_all_audit_fields(con):
    _decide(con, match_score=12.5, match_probability=0.97, queue_rank=3)
    row = rs.audit_trail(con).iloc[0]
    assert (row.a_id, row.b_id, row.decision, row.reviewer) == ("CRM-1", "WEB-2", "merge", "asha")
    assert row.reason == "same phone and e-mail" and row.match_score == 12.5 and row.queue_rank == 3
    assert row.decided_at_utc.endswith("+00:00")


def test_pair_order_does_not_matter(con):
    _decide(con, a="WEB-2", b="CRM-1")
    assert list(rs.audit_trail(con, "CRM-1", "WEB-2").a_id) == ["CRM-1"]
    assert rs.pair_key("B", "A") == ("A", "B")


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_reason_is_mandatory(con, bad):
    with pytest.raises(ValueError, match="reason"):
        rs.record_decision(con, "A", "B", rs.MERGE, bad, "asha")
    assert rs.audit_trail(con).empty


def test_reviewer_and_decision_are_validated(con):
    with pytest.raises(ValueError, match="reviewer"):
        rs.record_decision(con, "A", "B", rs.MERGE, "ok", " ")
    with pytest.raises(ValueError, match="decision"):
        rs.record_decision(con, "A", "B", "delete_everything", "ok", "asha")
    with pytest.raises(ValueError, match="two different"):
        rs.record_decision(con, "A", "A", rs.MERGE, "ok", "asha")


def test_audit_trail_cannot_be_rewritten(con):
    _decide(con)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("UPDATE review_decisions SET decision = 'keep_separate'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("DELETE FROM review_decisions")
    assert len(rs.audit_trail(con)) == 1


def test_changing_your_mind_keeps_history_and_latest_wins(con):
    _decide(con, decision=rs.MERGE)
    _decide(con, decision=rs.KEEP_SEPARATE, reason="different cities on review")
    assert len(rs.audit_trail(con)) == 2
    cur = rs.current_decisions(con)
    assert list(cur.decision) == ["keep_separate"]
    assert rs.approved_merges(con).empty and len(rs.approved_separations(con)) == 1


def test_pending_queue_hides_final_verdicts_but_keeps_deferred(con):
    queue = pd.DataFrame({"a_id": ["A", "C", "E"], "b_id": ["B", "D", "F"]})
    rs.record_decision(con, "B", "A", rs.MERGE, "r", "x")
    rs.record_decision(con, "C", "D", rs.NEEDS_INVESTIGATION, "check phone owner", "x")
    assert list(rs.pending_queue(queue, con).a_id) == ["C", "E"]
    s = rs.decision_summary(con)
    assert s["pairs_reviewed"] == 2 and s["merge"] == 1 and s["needs_investigation"] == 1 and s["total_actions"] == 2


def test_empty_queue_and_empty_store(con):
    assert rs.pending_queue(pd.DataFrame(), con).empty
    assert rs.current_decisions(con).empty
    assert rs.decision_summary(con)["pairs_reviewed"] == 0


def _row(levels, **vals):
    base = {f"{s}_{f}": "" for s in "ab" for f in ("full_name", "phone", "email", "city", "pincode", "signup_date")}
    base.update(vals)
    base["levels"] = levels
    return pd.Series(base)


def test_explain_pair_marks_supporting_contradicting_and_missing_fields():
    row = _row(
        "first=3 last=0 phone=2 email=- city=1 pincode=2 date=2", a_full_name="Rahul Sharma", b_full_name="Rahul Verma"
    )
    ex = rs.explain_pair(row).set_index("field").evidence
    assert ex["full_name"] == "contradicts"  # one name part differs
    assert ex["phone"] == "supports" and ex["pincode"] == "supports"
    assert ex["email"] == "missing"
    assert "contradict" in rs.risk_note(rs.explain_pair(row))


def test_risk_note_is_low_when_everything_supports():
    row = _row("first=3 last=3 phone=2 email=2 city=2 pincode=2 date=2")
    assert "low risk" in rs.risk_note(rs.explain_pair(row))


def test_review_store_never_touches_the_answer_key():
    text = open(rs.__file__, encoding="utf-8").read()
    for forbidden in ("answer_key", "GROUND_TRUTH_PATH", "customer_master.evaluation", "customer_master.heldout"):
        assert forbidden not in text.replace("Separate from evaluation", "")
