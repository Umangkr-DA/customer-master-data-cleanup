import tempfile
from pathlib import Path

from customer_master.profile import COLUMNS, load_raw_sources

HEADER = ",".join(COLUMNS)


def write(d: Path, crm: str, web: str, sales: str):
    for name, body in (("crm", crm), ("web", web), ("sales", sales)):
        (d / f"{name}.csv").write_text(body, encoding="utf-8")


def row(i, system):
    return f"{i},{system},Asha Rao,9333333334,a@x.com,Bhopal,462001,2022-01-01\n"


def expect_error(exc, fn):
    try:
        fn()
    except exc as e:
        return str(e)
    raise AssertionError("expected an error")


def test_valid_input_loads_all_sources():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        write(d, HEADER + "\n" + row("C1", "CRM"), HEADER + "\n" + row("W1", "Web"), HEADER + "\n" + row("S1", "Sales"))
        assert len(load_raw_sources(d)) == 3


def test_missing_file_fails_clearly():
    with tempfile.TemporaryDirectory() as t:
        assert "not found" in expect_error(FileNotFoundError, lambda: load_raw_sources(Path(t)))


def test_empty_file_is_rejected():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        write(d, HEADER + "\n", HEADER + "\n" + row("W1", "Web"), HEADER + "\n" + row("S1", "Sales"))
        assert "no records" in expect_error(ValueError, lambda: load_raw_sources(d))


def test_missing_column_is_rejected():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        bad = "record_id,source_system\nC1,CRM\n"
        write(d, bad, HEADER + "\n" + row("W1", "Web"), HEADER + "\n" + row("S1", "Sales"))
        assert "missing columns" in expect_error(ValueError, lambda: load_raw_sources(d))


def test_unexpected_extra_columns_are_ignored_not_fatal():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        extra = HEADER + ",notes\n" + row("C1", "CRM").rstrip("\n") + ",hello\n"
        write(d, extra, HEADER + "\n" + row("W1", "Web"), HEADER + "\n" + row("S1", "Sales"))
        assert list(load_raw_sources(d).columns) == COLUMNS


def test_duplicate_source_identifiers_are_rejected():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        write(d, HEADER + "\n" + row("X1", "CRM"), HEADER + "\n" + row("X1", "Web"), HEADER + "\n" + row("S1", "Sales"))
        assert "unique" in expect_error(ValueError, lambda: load_raw_sources(d))


def test_blank_values_are_kept_as_empty_strings_not_dropped():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        blank = HEADER + "\nC1,CRM,,,,,,\n"
        write(d, blank, HEADER + "\n" + row("W1", "Web"), HEADER + "\n" + row("S1", "Sales"))
        assert len(load_raw_sources(d)) == 3
