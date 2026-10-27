import pandas as pd

from customer_master.demo_data import inspect_golden, merged_golden_ids


def _data():
    raw = pd.DataFrame(
        {"record_id": ["CRM-1", "WEB-2", "CRM-3"], "full_name": ["A B", "A B", "C D"], "phone": ["1", "1", "2"]}
    ).set_index("record_id")
    golden = pd.DataFrame(
        {
            "golden_id": ["G1", "G2"],
            "full_name": ["A B", "C D"],
            "phone": ["1", "2"],
            "email": ["", ""],
            "city": ["", ""],
            "pincode": ["", ""],
            "signup_date": ["", ""],
            "name_source": ["CRM", "CRM"],
            "phone_source": ["Web", "CRM"],
            "email_source": ["", ""],
            "address_source": ["", ""],
            "date_source": ["", ""],
            "n_source_records": ["2", "1"],
            "merged_record_ids": ["CRM-1 | WEB-2", "CRM-3"],
        }
    ).set_index("golden_id")
    pairs = pd.DataFrame({"a_id": ["CRM-1"], "b_id": ["WEB-2"], "phone_match": ["1"]})
    return {"raw": raw, "golden": golden, "pairs": pairs}


def test_only_merged_records_are_listed():
    assert merged_golden_ids(_data()["golden"]) == ["G1"]


def test_inspect_returns_sources_evidence_and_decisions():
    info = inspect_golden("G1", _data())
    assert list(info["sources"].record_id) == ["CRM-1", "WEB-2"]
    assert len(info["evidence"]) == 1
    assert info["decisions"].set_index("field").loc["phone", "won_by"] == "Web"


def test_single_record_has_no_evidence():
    assert inspect_golden("G2", _data())["evidence"].empty
