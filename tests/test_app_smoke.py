"""Every dashboard section must render without an exception (needs the optional streamlit dependency)."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"
SECTIONS = {
    "Executive dashboard": "Executive dashboard",
    "Data quality": "Data quality analysis",
    "Golden record explorer": "Golden record explorer",
    "Matching evaluation": "Matching evaluation",
    "Human review": "Human review queue",
    "Impact simulator": "Business impact simulator",
}


@pytest.mark.parametrize("section,header", SECTIONS.items())
def test_section_renders(section, header):
    at = AppTest.from_file(str(APP), default_timeout=120).run()
    at.sidebar.radio[0].set_value(section).run()
    assert not at.exception, [e.value for e in at.exception]
    assert header in [h.value for h in at.header]


def test_review_decision_round_trip_through_the_app(tmp_path, monkeypatch):
    from customer_master import review_store as rs

    db = tmp_path / "decisions.db"
    monkeypatch.setenv(rs.DB_ENV_VAR, str(db))
    at = AppTest.from_file(str(APP), default_timeout=120).run()
    at.sidebar.radio[0].set_value("Human review").run()
    pending_before = int(at.metric[0].value.replace(",", ""))

    at.text_area[0].set_value("same phone and e-mail, only a name typo")
    [b for b in at.button if b.label == "Save decision"][0].click().run()
    assert not at.exception

    con = rs.connect(db)
    trail = rs.audit_trail(con)
    assert len(trail) == 1 and trail.decision[0] == "merge" and trail.reason[0].startswith("same phone")
    assert int(at.metric[0].value.replace(",", "")) == pending_before - 1  # the pair left the pending list
    con.close()


def test_review_form_rejects_an_empty_reason(tmp_path, monkeypatch):
    from customer_master import review_store as rs

    monkeypatch.setenv(rs.DB_ENV_VAR, str(tmp_path / "d.db"))
    at = AppTest.from_file(str(APP), default_timeout=120).run()
    at.sidebar.radio[0].set_value("Human review").run()
    [b for b in at.button if b.label == "Save decision"][0].click().run()
    assert [e.value for e in at.error] == ["a reason is required for every decision"]
    assert rs.audit_trail(rs.connect(tmp_path / "d.db")).empty
