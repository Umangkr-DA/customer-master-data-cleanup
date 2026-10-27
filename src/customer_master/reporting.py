"""Step 6 - build reports/customer_data_quality_report.xlsx from the pipeline outputs.

Counts are written as inputs (blue); every rate, difference and total is a live Excel formula,
so the workbook recalculates if a count is edited. Nothing is typed in by hand: all numbers come
from outputs/*.csv and outputs/metrics.json.
"""

import json

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from customer_master.common import OUTPUT_DIR, REPORT_DIR, REPORT_PATH, RULES, get_logger

log = get_logger("report")

# ----------------------------------------------------------------------------- styles
FONT = "Arial"
BASE = Font(name=FONT, size=10)
BOLD = Font(name=FONT, size=10, bold=True)
TITLE = Font(name=FONT, size=16, bold=True, color="1F3864")
SUBTITLE = Font(name=FONT, size=10, italic=True, color="595959")
SECTION = Font(name=FONT, size=12, bold=True, color="1F3864")
HEADER = Font(name=FONT, size=10, bold=True, color="FFFFFF")
INPUT = Font(name=FONT, size=10, color="0000FF")  # counts produced by the pipeline
LINK = Font(name=FONT, size=10, color="008000")  # pulled from another sheet
KPI_VALUE = Font(name=FONT, size=18, bold=True, color="1F3864")
KPI_LABEL = Font(name=FONT, size=9, color="595959")
NOTE = Font(name=FONT, size=9, italic=True, color="595959")
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
TOTAL_FILL = PatternFill("solid", fgColor="DDEBF7")
KPI_FILL = PatternFill("solid", fgColor="F2F2F2")
THIN = Side(style="thin", color="BFBFBF")
BOX = Border(top=THIN, bottom=THIN, left=THIN, right=THIN)
PCT, INT, PP = "0.0%", "#,##0", '+0.0" pp"'


def header(ws, row: int, labels: list[str], col: int = 1) -> None:
    for i, text in enumerate(labels):
        c = ws.cell(row=row, column=col + i, value=text)
        c.font, c.fill, c.border = HEADER, HEADER_FILL, BOX
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 30


def put(ws, ref: str, value, font=BASE, fmt=None, fill=None, wrap=False):
    c = ws[ref]
    c.value, c.font, c.border = value, font, BOX
    if fmt:
        c.number_format = fmt
    if fill:
        c.fill = fill
    if wrap:
        c.alignment = Alignment(wrap_text=True, vertical="top")
    return c


def set_widths(ws, widths: list[float]) -> None:
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def title(ws, text: str, sub: str) -> None:
    ws["A1"], ws["A1"].font = text, TITLE
    ws["A2"], ws["A2"].font = sub, SUBTITLE
    ws.sheet_view.showGridLines = False


def add_matcher_comparison(wb) -> None:
    """Held-out matcher results, 20-seed intervals and scale rows, read from the generated files (not recomputed)."""
    from openpyxl.chart import BarChart, Reference

    from customer_master import headline

    try:
        h = headline.build(OUTPUT_DIR)
    except FileNotFoundError:  # a partial checkout without the experiment outputs: skip the sheet
        log.info("matcher comparison sheet skipped (experiment outputs not found)")
        return
    ws = wb.create_sheet("Matcher Comparison", index=wb.sheetnames.index("Matching") + 1)
    title(
        ws,
        "Matcher comparison on hard held-out data",
        "Held-out set never used for tuning; configuration frozen on dev first (outputs/heldout_ledger.json). Values are copied from generated files.",
    )
    ws["A4"], ws["A4"].font = "1. Held-out results (one evaluation per frozen configuration)", SECTION
    header(ws, 5, ["Matcher", "Precision", "Recall", "F1", "B-cubed F1", "Candidate pairs", "Runtime (s)"])
    for i, (name, r) in enumerate(h["heldout"].items()):
        row = 6 + i
        put(ws, f"A{row}", name + (" (recommended)" if name == h["frozen_matcher"] else ""), BOLD)
        for col, key, fmt in (("B", "precision", PCT), ("C", "recall", PCT), ("D", "f1", PCT), ("E", "bcubed_f1", PCT),
                              ("F", "candidate_pairs", INT), ("G", "runtime_s", "0.00")):
            put(ws, f"{col}{row}", r[key], INPUT, fmt)
    ch = BarChart()
    ch.type, ch.title, ch.y_axis.title = "col", "Held-out precision, recall, F1", None
    ch.add_data(Reference(ws, min_col=2, max_col=4, min_row=5, max_row=8), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=1, min_row=6, max_row=8))
    ch.y_axis.scaling.min, ch.y_axis.scaling.max, ch.y_axis.number_format = 0, 1, "0%"
    ch.height, ch.width = 8, 16
    ws.add_chart(ch, "I4")

    ws["A11"], ws["A11"].font = f"2. {h['multiseed'][h['frozen_matcher']]['n_seeds']} unseen seeds: mean and 95% interval", SECTION
    header(ws, 12, ["Matcher", "Metric", "Mean", "95% low", "95% high"])
    row = 13
    for name, ms in h["multiseed"].items():
        for metric in ("precision", "recall", "f1"):
            put(ws, f"A{row}", name)
            put(ws, f"B{row}", metric)
            for col, key in (("C", "mean"), ("D", "low"), ("E", "high")):
                put(ws, f"{col}{row}", ms[metric][key], INPUT, PCT)
            row += 1

    row += 1
    ws[f"A{row}"], ws[f"A{row}"].font = "3. Scale benchmark (synthetic hard data)", SECTION
    row += 1
    header(ws, row, ["Variant @ records", "Precision", "Recall", "Matching (s)", "Peak memory (MB)", "Status"])
    for key, r in h["scale"].items():
        row += 1
        ok = r["status"] == "ok"
        put(ws, f"A{row}", key)
        put(ws, f"B{row}", r["precision"] if ok else "n/a", INPUT, PCT)
        put(ws, f"C{row}", r["recall"] if ok else "n/a", INPUT, PCT)
        put(ws, f"D{row}", r["matching_s"] if ok else "n/a", INPUT, "0.0")
        put(ws, f"E{row}", r["peak_rss_mb"] if ok else "n/a", INPUT, INT)
        put(ws, f"F{row}", "ok" if ok else "FAILED (MemoryError, 6 GB cap)")
    row += 2
    notes = [
        f"Review queue (simulated perfect reviewer): recall {h['review_simulation']['recall_before']:.1%} before, "
        f"{h['review_simulation']['recall']['mean']:.1%} after {h['review_simulation']['reviewed']} ranked pairs reviewed.",
        f"Public Febrl febrl3: precision {h['febrl3']['precision']:.1%}, recall {h['febrl3']['recall']:.1%}, "
        f"F1 {h['febrl3']['f1']:.1%}.",
        "The Dashboard, Data Quality, Matching and other sheets describe the original easy-data, exact-key SQL pipeline.",
    ]
    for n in notes:
        ws[f"A{row}"], ws[f"A{row}"].font = n, NOTE
        row += 1
    set_widths(ws, [30, 12, 12, 14, 18, 30, 14])


def add_executive_summary(wb, golden_all_pass: float, raw_all_pass: float) -> None:
    """First sheet: the credible (hard, held-out) results lead; easy-data sheets are labelled as such."""
    from customer_master import headline

    try:
        h = headline.build(OUTPUT_DIR)
    except FileNotFoundError:
        log.info("executive summary sheet skipped (experiment outputs not found)")
        return
    ws = wb.create_sheet("Executive Summary", index=0)
    title(
        ws,
        "Customer master data: executive summary",
        "Matching results are from the hard held-out set (never used for tuning). Synthetic data; all values copied from generated files.",
    )
    fs = h["heldout"][h["frozen_matcher"]]
    ms = h["multiseed"][h["frozen_matcher"]]
    rv = h["review_simulation"]
    tiles = [
        ("Match F1 (held-out)", fs["f1"], PCT),
        ("Match precision", fs["precision"], PCT),
        ("Match recall", fs["recall"], PCT),
        ("Recall after 500 reviews (simulated)", rv["recall"]["mean"], PCT),
        ("All 6 rules: raw (easy data)", raw_all_pass, PCT),
        ("All 6 rules: golden (easy data)", golden_all_pass, PCT),
    ]
    for k, (lab, val, fmt) in enumerate(tiles):
        col = 1 + 2 * (k % 3)
        row = 4 if k < 3 else 8
        c1, c2 = get_column_letter(col), get_column_letter(col + 1)
        ws.merge_cells(f"{c1}{row}:{c2}{row}")
        ws.merge_cells(f"{c1}{row + 1}:{c2}{row + 1}")
        for rr in (row, row + 1):
            for cc in (c1, c2):
                ws[f"{cc}{rr}"].fill, ws[f"{cc}{rr}"].border = KPI_FILL, BOX
        ws[f"{c1}{row}"], ws[f"{c1}{row}"].font = lab, KPI_LABEL
        ws[f"{c1}{row}"].alignment = Alignment(horizontal="center", wrap_text=True)
        ws[f"{c1}{row + 1}"], ws[f"{c1}{row + 1}"].font = val, KPI_VALUE
        ws[f"{c1}{row + 1}"].number_format = fmt
        ws[f"{c1}{row + 1}"].alignment = Alignment(horizontal="center")
        ws.row_dimensions[row + 1].height = 30
    ws["A12"], ws["A12"].font = "Matcher comparison (held-out)", SECTION
    header(ws, 13, ["Matcher", "Precision", "Recall", "F1"])
    for i, (name, r) in enumerate(h["heldout"].items()):
        row = 14 + i
        put(ws, f"A{row}", name + (" *" if name == h["frozen_matcher"] else ""), BOLD)
        for col, key in (("B", "precision"), ("C", "recall"), ("D", "f1")):
            put(ws, f"{col}{row}", r[key], INPUT, PCT)
    ch = BarChart()
    ch.type, ch.title = "col", "Held-out precision, recall, F1"
    ch.add_data(Reference(ws, min_col=2, max_col=4, min_row=13, max_row=16), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=1, min_row=14, max_row=16))
    ch.y_axis.scaling.min, ch.y_axis.scaling.max, ch.y_axis.number_format = 0, 1, "0%"
    ch.height, ch.width = 8, 16
    ws.add_chart(ch, "A19")
    row = 36
    ws[f"A{row}"], ws[f"A{row}"].font = "What this shows", SECTION
    lines = [
        f"* Fellegi-Sunter (* = recommended) reaches {fs['f1']:.1%} F1 on held-out data; over {ms['n_seeds']} unseen seeds F1 is "
        f"{ms['f1']['low']:.1%}-{ms['f1']['high']:.1%} (95% interval of the mean).",
        "* Precision stays high for all three matchers; recall is where they differ, because combining weak evidence finds duplicates a single rule misses.",
        f"* Reviewing {rv['reviewed']} ranked pairs lifts simulated recall from {rv['recall_before']:.1%} to {rv['recall']['mean']:.1%} (perfect reviewer: a simulation).",
        "* Standardization drives most of the data-quality gain (formatting repair); merging adds a smaller lift. See the Data Quality sheet.",
        "* Sheets Dashboard, Data Quality, Matching, Cleaning Log, By Source and Golden Records describe the original easy-data, exact-key pipeline.",
    ]
    for t in lines:
        row += 1
        ws[f"A{row}"], ws[f"A{row}"].font = t, BASE
    set_widths(ws, [24, 14, 14, 14, 14, 14])


def main() -> None:
    metrics = json.loads((OUTPUT_DIR / "metrics.json").read_text())
    ins = metrics["insights"]
    vs = pd.read_csv(OUTPUT_DIR / "validation_summary.csv")
    rules = pd.read_csv(OUTPUT_DIR / "match_rule_summary.csv")
    sizes = pd.read_csv(OUTPUT_DIR / "cluster_size_distribution.csv")
    by_source = pd.read_csv(OUTPUT_DIR / "quality_by_source.csv")
    golden = pd.read_csv(OUTPUT_DIR / "golden_records.csv", dtype=str)
    profile = pd.read_csv(OUTPUT_DIR / "data_profile.csv")
    cleaning = pd.read_csv(OUTPUT_DIR / "cleaning_log.csv")

    wb = Workbook()
    dash = wb.active
    dash.title = "Dashboard"

    # ======================================================================== Data Quality
    dq = wb.create_sheet("Data Quality")
    title(
        dq,
        "Data quality scorecard: raw -> standardized -> golden",
        "Same six rules at every stage. A blank value FAILS its rule, so values left blank on purpose (never guessed) show up as failures.",
    )
    header(
        dq,
        4,
        [
            "Rule",
            "Field",
            "Definition",
            "Raw: records",
            "Raw: passed",
            "Raw: pass %",
            "Standardized: records",
            "Standardized: passed",
            "Standardized: pass %",
            "Golden: records",
            "Golden: passed",
            "Golden: pass %",
            "Raw -> Standardized",
            "Raw -> Golden",
        ],
    )
    dq.row_dimensions[4].height = 42
    first = 5
    for i, r in enumerate(vs.itertuples(), start=first):
        is_all = r.rule_id == "ALL"
        fill = TOTAL_FILL if is_all else None
        f = BOLD if is_all else BASE
        put(dq, f"A{i}", r.rule_id, BOLD, fill=fill)
        put(dq, f"B{i}", r.field, f, fill=fill)
        put(dq, f"C{i}", r.definition, f, fill=fill)
        for cols, (tot, ok) in zip(
            ("DEF", "GHI", "JKL"),
            ((r.raw_total, r.raw_passed), (r.std_total, r.std_passed), (r.golden_total, r.golden_passed)),
        ):
            put(dq, f"{cols[0]}{i}", int(tot), INPUT, INT, fill)
            put(dq, f"{cols[1]}{i}", int(ok), INPUT, INT, fill)
            put(dq, f"{cols[2]}{i}", f"=IFERROR({cols[1]}{i}/{cols[0]}{i},0)", f, PCT, fill)
        put(dq, f"M{i}", f"=(I{i}-F{i})*100", f, PP, fill)
        put(dq, f"N{i}", f"=(L{i}-F{i})*100", f, PP, fill)
    last = first + len(vs) - 1
    all_row = last
    for col in "FIL":
        dq.conditional_formatting.add(
            f"{col}{first}:{col}{last - 1}",
            ColorScaleRule(
                start_type="num",
                start_value=0.5,
                start_color="F8CBAD",
                mid_type="num",
                mid_value=0.8,
                mid_color="FFEB9C",
                end_type="num",
                end_value=1,
                end_color="C6E0B4",
            ),
        )
    dq[f"A{last + 2}"] = (
        "Blue = counts from the pipeline (outputs/validation_summary.csv). Black = formulas. "
        "Raw/standardized are measured over source records; golden over golden records."
    )
    dq[f"A{last + 2}"].font = NOTE
    chart = BarChart()
    chart.type, chart.grouping = "col", "clustered"
    chart.title, chart.y_axis.title = "Pass rate by rule", "% of records passing"
    chart.y_axis.number_format, chart.y_axis.scaling.min, chart.y_axis.scaling.max = "0%", 0, 1
    for col in (6, 9, 12):
        chart.add_data(Reference(dq, min_col=col, min_row=4, max_row=last - 1), titles_from_data=True)
    chart.set_categories(Reference(dq, min_col=2, min_row=first, max_row=last - 1))
    chart.height, chart.width = 9, 22
    dq.add_chart(chart, f"A{last + 4}")
    set_widths(dq, [8, 14, 62, 11, 11, 11, 14, 14, 14, 11, 11, 11, 14, 14])
    dq.freeze_panes = "D5"

    # ======================================================================== Matching
    mt = wb.create_sheet("Matching")
    title(
        mt,
        "Entity resolution: results and evaluation",
        "Original SQL pipeline: deterministic exact-key rules on the easy dataset. Evaluated at record-PAIR level against a hidden ground-truth key.",
    )
    mt["A4"], mt["A4"].font = "1. Evaluation against ground truth", SECTION
    header(mt, 5, ["Metric", "Value", "Definition"])
    eval_rows = [
        ("Source records", metrics["raw_records"], INT, "Rows from CRM + Web + Sales"),
        ("Golden records", metrics["golden_records"], INT, "One row per matched cluster"),
        ("Records merged", "=B6-B7", INT, "Source records minus golden records"),
        ("True customers (answer key)", metrics["true_customers"], INT, "Distinct entity_id in the ground truth"),
        ("True duplicate pairs", metrics["true_duplicate_pairs"], INT, "Record pairs that belong to the same customer"),
        (
            "True positives (pairs)",
            metrics["true_positive_pairs"],
            INT,
            "Merged by the pipeline AND truly the same customer",
        ),
        (
            "False positives (pairs)",
            metrics["false_positive_pairs"],
            INT,
            "Merged by the pipeline but truly different customers",
        ),
        (
            "False negatives (pairs)",
            metrics["false_negative_pairs"],
            INT,
            "True duplicate pairs the pipeline did not merge",
        ),
        ("Precision", "=IFERROR(B11/(B11+B12),0)", PCT, "TP / (TP + FP)"),
        ("Recall", "=IFERROR(B11/(B11+B13),0)", PCT, "TP / (TP + FN)"),
        ("F1 score", "=IFERROR(2*B14*B15/(B14+B15),0)", PCT, "Harmonic mean of precision and recall (pair level)"),
        ("Multi-record customers", metrics["multi_record_customers"], INT, "True customers with 2+ source records"),
        (
            "...reproduced exactly",
            metrics["multi_record_customers_reproduced_exactly"],
            INT,
            "All their records merged into one cluster with nothing else",
        ),
        (
            "Over-merged clusters",
            metrics["over_merged_clusters"],
            INT,
            "Clusters containing records of more than one true customer",
        ),
        (
            "Redundant records per answer key",
            "=B6-B9",
            INT,
            "Source records minus true customers: rows that SHOULD be removed",
        ),
        (
            "Redundant records left unmerged",
            "=B7-B9",
            INT,
            "Golden records minus true customers (record-level view of the missed duplicates)",
        ),
    ]
    for i, (lab, val, fmt, note) in enumerate(eval_rows, start=6):
        key = lab in ("Precision", "Recall", "F1 score")
        put(mt, f"A{i}", lab, BOLD if key else BASE, fill=TOTAL_FILL if key else None)
        put(
            mt,
            f"B{i}",
            val,
            BOLD if key else (BASE if str(val).startswith("=") else INPUT),
            fmt,
            TOTAL_FILL if key else None,
        )
        put(mt, f"C{i}", note)
    r = 6 + len(eval_rows) + 1
    mt[f"A{r}"], mt[f"A{r}"].font = "2. Match rules applied (accepted pairs)", SECTION
    header(mt, r + 1, ["Rule (keys that agree)", "Confidence", "Pairs accepted", "% of accepted pairs"])
    rs = r + 2
    for i, x in enumerate(rules.itertuples(), start=rs):
        put(mt, f"A{i}", x.match_rule)
        put(mt, f"B{i}", x.match_confidence)
        put(mt, f"C{i}", int(x.pairs), INPUT, INT)
        put(mt, f"D{i}", f"=IFERROR(C{i}/C${rs + len(rules)},0)", BASE, PCT)
    re_ = rs + len(rules)
    put(mt, f"A{re_}", "Total", BOLD, fill=TOTAL_FILL)
    put(mt, f"B{re_}", "", fill=TOTAL_FILL)
    put(mt, f"C{re_}", f"=SUM(C{rs}:C{re_ - 1})", BOLD, INT, TOTAL_FILL)
    put(mt, f"D{re_}", f"=SUM(D{rs}:D{re_ - 1})", BOLD, PCT, TOTAL_FILL)
    mt[f"A{re_ + 1}"] = (
        "High = 2+ independent keys agree. Medium = 1 key agrees and the names agree. Keys: phone, email, full name + pincode."
    )
    mt[f"A{re_ + 1}"].font = NOTE

    r = re_ + 3
    mt[f"A{r}"], mt[f"A{r}"].font = "3. Duplicate groups (golden records by number of source records)", SECTION
    header(mt, r + 1, ["Source records merged", "Golden records", "Source records covered"])
    gs = r + 2
    for i, x in enumerate(sizes.itertuples(), start=gs):
        put(mt, f"A{i}", int(x.size), BASE, INT)
        put(mt, f"B{i}", int(x.golden_records), INPUT, INT)
        put(mt, f"C{i}", f"=A{i}*B{i}", BASE, INT)
    ge = gs + len(sizes)
    put(mt, f"A{ge}", "Total", BOLD, fill=TOTAL_FILL)
    put(mt, f"B{ge}", f"=SUM(B{gs}:B{ge - 1})", BOLD, INT, TOTAL_FILL)
    put(mt, f"C{ge}", f"=SUM(C{gs}:C{ge - 1})", BOLD, INT, TOTAL_FILL)

    r = ge + 2
    mt[f"A{r}"], mt[f"A{r}"].font = "4. Error analysis: why true duplicate pairs were missed (false negatives)", SECTION
    header(mt, r + 1, ["Reason (from standardized fields)", "Pairs", "% of misses"])
    ms = r + 2
    reasons = list(metrics["missed_pair_reasons"].items())
    for i, (reason, n) in enumerate(reasons, start=ms):
        put(mt, f"A{i}", reason)
        put(mt, f"B{i}", int(n), INPUT, INT)
        put(mt, f"C{i}", f"=IFERROR(B{i}/B${ms + len(reasons)},0)", BASE, PCT)
    me = ms + len(reasons)
    put(mt, f"A{me}", "Total false negatives", BOLD, fill=TOTAL_FILL)
    put(mt, f"B{me}", f"=SUM(B{ms}:B{me - 1})", BOLD, INT, TOTAL_FILL)
    put(mt, f"C{me}", f"=SUM(C{ms}:C{me - 1})", BOLD, PCT, TOTAL_FILL)
    put(mt, f"A{me + 1}", "Check: equals false negatives above", NOTE)
    put(mt, f"B{me + 1}", f'=IF(B{me}=B13,"OK","MISMATCH")', BOLD)
    mt[f"A{me + 3}"], mt[f"A{me + 3}"].font = (
        "5. Pairs rejected by the name guard (shared key, but the matcher refused to merge)",
        SECTION,
    )
    header(mt, me + 4, ["Reason", "Pairs"])
    put(mt, f"A{me + 5}", "Names conflict (different people, e.g. relatives sharing a phone)")
    put(mt, f"B{me + 5}", ins["rejected_pairs_name_conflict"], INPUT, INT)
    put(mt, f"A{me + 6}", "A name is blank, so a single shared key is not trusted")
    put(mt, f"B{me + 6}", ins["rejected_pairs_name_blank"], INPUT, INT)
    set_widths(mt, [62, 16, 70, 18])
    mt.freeze_panes = "A4"

    # ======================================================================== Cleaning Log
    cl = wb.create_sheet("Cleaning Log")
    title(
        cl,
        "What standardization did, field by field",
        "Unfixable values are left blank, never guessed. Counts are source-record values.",
    )
    outcomes = ["already clean", "standardized", "unfixable -> left blank", "blank in source"]
    header(cl, 4, ["Field"] + outcomes + ["Total", "% repaired or blanked", "% left blank (unfixable)"])
    pivot = cleaning.pivot_table(index="field", columns="outcome", values="n", aggfunc="sum", fill_value=0)
    for i, field in enumerate(["full_name", "phone", "email", "city", "pincode", "signup_date"], start=5):
        put(cl, f"A{i}", field, BOLD)
        for j, o in enumerate(outcomes):
            put(cl, f"{get_column_letter(2 + j)}{i}", int(pivot.loc[field].get(o, 0)), INPUT, INT)
        put(cl, f"F{i}", f"=SUM(B{i}:E{i})", BASE, INT)
        put(cl, f"G{i}", f"=IFERROR((C{i}+D{i})/F{i},0)", BASE, PCT)
        put(cl, f"H{i}", f"=IFERROR(D{i}/F{i},0)", BASE, PCT)
    put(cl, "A11", "Total", BOLD, fill=TOTAL_FILL)
    for col in "BCDEF":
        put(cl, f"{col}11", f"=SUM({col}5:{col}10)", BOLD, INT, TOTAL_FILL)
    put(cl, "G11", "=IFERROR((C11+D11)/F11,0)", BOLD, PCT, TOTAL_FILL)
    put(cl, "H11", "=IFERROR(D11/F11,0)", BOLD, PCT, TOTAL_FILL)
    cl["A13"], cl["A13"].font = "Raw profile (before any cleaning)", SECTION
    header(
        cl, 14, ["Field", "Total values", "Missing / placeholder", "Missing %", "With stray spaces", "Distinct values"]
    )
    for i, p in enumerate(profile.itertuples(), start=15):
        put(cl, f"A{i}", p.field, BOLD)
        put(cl, f"B{i}", int(p.total_values), INPUT, INT)
        put(cl, f"C{i}", int(p.missing_or_placeholder), INPUT, INT)
        put(cl, f"D{i}", f"=IFERROR(C{i}/B{i},0)", BASE, PCT)
        put(cl, f"E{i}", int(p.with_stray_spaces), INPUT, INT)
        put(cl, f"F{i}", int(p.distinct_values), INPUT, INT)
    set_widths(cl, [16, 14, 16, 22, 16, 14, 20, 22])

    # ======================================================================== By Source
    bs = wb.create_sheet("By Source")
    title(bs, "Raw data quality by source system", "Share of RAW records passing all six rules, per source.")
    header(bs, 4, ["Source", "Records", "Passing all 6 (raw)", "Pass %"])
    for i, s in enumerate(by_source.itertuples(), start=5):
        put(bs, f"A{i}", s.source_system, BOLD)
        put(bs, f"B{i}", int(s.records), INPUT, INT)
        put(bs, f"C{i}", int(s.raw_pass), INPUT, INT)
        put(bs, f"D{i}", f"=IFERROR(C{i}/B{i},0)", BASE, PCT)
    e = 5 + len(by_source)
    put(bs, f"A{e}", "Total", BOLD, fill=TOTAL_FILL)
    put(bs, f"B{e}", f"=SUM(B5:B{e - 1})", BOLD, INT, TOTAL_FILL)
    put(bs, f"C{e}", f"=SUM(C5:C{e - 1})", BOLD, INT, TOTAL_FILL)
    put(bs, f"D{e}", f"=IFERROR(C{e}/B{e},0)", BOLD, PCT, TOTAL_FILL)
    bs[f"A{e + 2}"] = (
        "The three systems are very similar - on this synthetic data no source is meaningfully 'cleaner' than another."
    )
    bs[f"A{e + 2}"].font = NOTE
    set_widths(bs, [14, 12, 20, 12])

    # ======================================================================== Golden Records
    gr = wb.create_sheet("Golden Records")
    cols = [
        "golden_id",
        "full_name",
        "phone",
        "email",
        "city",
        "pincode",
        "signup_date",
        "n_source_records",
        "source_systems",
        "match_rules",
        "lowest_match_confidence",
        "name_source",
        "phone_source",
        "email_source",
        "address_source",
        "date_source",
        "merged_record_ids",
    ]
    header(gr, 1, [c.replace("_", " ").title() for c in cols])
    for ri, row in enumerate(golden[cols].itertuples(index=False), start=2):
        for ci, v in enumerate(row, start=1):
            if pd.isna(v):
                continue  # blank stays an empty cell: unfixable != guessed
            gr.cell(row=ri, column=ci, value=int(v) if cols[ci - 1] == "n_source_records" else v).font = BASE
    set_widths(gr, [11, 22, 13, 36, 12, 10, 12, 10, 16, 36, 12, 11, 11, 11, 12, 11, 44])
    gr.freeze_panes = "C2"
    gr.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{len(golden) + 1}"

    # ======================================================================== Methodology
    me_ws = wb.create_sheet("Methodology")
    title(me_ws, "Methodology", "How every number in this workbook was produced")
    text = (
        [
            ("PIPELINE", True),
            (
                "1 profile -> 2 standardize -> 3 match -> 4 golden records -> 5 validate & evaluate -> 6 report. Run: python scripts/run_pipeline.py",
                False,
            ),
            ("", False),
            ("STANDARDIZATION POLICY", True),
            (
                "Fix deterministic formatting only: case, spacing, +91/0 phone prefixes, date formats, known email-domain typos, city aliases/typos.",
                False,
            ),
            (
                "Ambiguous or invalid values are left blank. dd/mm/yyyy is assumed for slash dates. A pincode that contradicts a known city is blanked.",
                False,
            ),
            ("", False),
            ("MATCHING (deterministic, explainable - no machine learning)", True),
            (
                "Candidates share a standardized phone, OR email, OR (full name + full pincode). NULL keys never match.",
                False,
            ),
            (
                "Name guard: same surname AND same first name (or an initial of it). Names missing -> require phone AND email to agree.",
                False,
            ),
            (
                "Clusters = connected components of accepted pairs. Confidence: High = 2+ keys agree; Medium = 1 key + names agree.",
                False,
            ),
            ("", False),
            ("SURVIVORSHIP (field level, non-NULL always beats NULL)", True),
            (
                "Name: longest valid name, tie CRM > Sales > Web. Phone: Sales > CRM > Web. Email: Web > CRM > Sales.",
                False,
            ),
            (
                "City+pincode: most complete consistent pair, tie CRM > Sales > Web; gaps filled only from agreeing records. Signup date: earliest.",
                False,
            ),
            ("", False),
            ("VALIDATION RULES", True),
        ]
        + [(f"{k}  {v[0]}: {v[1]}", False) for k, v in RULES.items()]
        + [
            ("", False),
            ("EVALUATION", True),
            (
                "Ground truth = hidden answer key written by the data generator; read only in step 5. Precision/recall are over record PAIRS.",
                False,
            ),
            (
                "The matching rules were refined after a first run (a name+pincode key was added after recall was 89.1%); this is disclosed in the README.",
                False,
            ),
            (
                "Synthetic data: noise levels are chosen by the author, so absolute numbers are not predictions for real data.",
                False,
            ),
        ]
    )
    for i, (t, b) in enumerate(text, start=4):
        me_ws[f"A{i}"], me_ws[f"A{i}"].font = t, (BOLD if b else BASE)
    me_ws.column_dimensions["A"].width = 150

    # ======================================================================== Dashboard
    title(
        dash,
        "Easy-data pipeline dashboard (original exact-key SQL pipeline)",
        "Optimistic by design: easy synthetic data, seed 42. For the credible held-out matching results see the Executive Summary sheet.",
    )
    tiles = [
        ("Source records", "='Data Quality'!D" + str(all_row), INT),
        ("Golden records", "=Matching!B7", INT),
        ("Records merged", "=Matching!B8", INT),
        ("Match precision", "=Matching!B14", PCT),
        ("Pair-level recall", "=Matching!B15", PCT),
        ("Pass all 6 rules: golden", f"='Data Quality'!L{all_row}", PCT),
    ]
    for k, (lab, f, fmt) in enumerate(tiles):
        col = 1 + 2 * k if k < 3 else 1 + 2 * (k - 3)
        row = 4 if k < 3 else 8
        c1, c2 = get_column_letter(col), get_column_letter(col + 1)
        dash.merge_cells(f"{c1}{row}:{c2}{row}")
        dash.merge_cells(f"{c1}{row + 1}:{c2}{row + 1}")
        for cc in (c1, c2):
            for rr in (row, row + 1):
                dash[f"{cc}{rr}"].fill, dash[f"{cc}{rr}"].border = KPI_FILL, BOX
        dash[f"{c1}{row}"], dash[f"{c1}{row}"].font = lab, KPI_LABEL
        dash[f"{c1}{row}"].alignment = Alignment(horizontal="center")
        dash[f"{c1}{row + 1}"], dash[f"{c1}{row + 1}"].font, dash[f"{c1}{row + 1}"].number_format = f, KPI_VALUE, fmt
        dash[f"{c1}{row + 1}"].alignment = Alignment(horizontal="center")
        dash.row_dimensions[row + 1].height = 30
    dash["A12"], dash["A12"].font = "Validation: records passing all six rules", SECTION
    header(dash, 13, ["Stage", "Records", "Passed", "Pass %", "Change vs raw"])
    for i, (stage, tc, pc, rc) in enumerate(
        [("Raw", "D", "E", "F"), ("Standardized", "G", "H", "I"), ("Golden", "J", "K", "L")], start=14
    ):
        put(dash, f"A{i}", stage, BOLD)
        put(dash, f"B{i}", f"='Data Quality'!{tc}{all_row}", LINK, INT)
        put(dash, f"C{i}", f"='Data Quality'!{pc}{all_row}", LINK, INT)
        put(dash, f"D{i}", f"='Data Quality'!{rc}{all_row}", LINK, PCT)
        put(dash, f"E{i}", f"=(D{i}-D$14)*100", BASE, PP)
    dash.conditional_formatting.add(
        "D14:D16",
        ColorScaleRule(
            start_type="num",
            start_value=0,
            start_color="F8CBAD",
            mid_type="num",
            mid_value=0.5,
            mid_color="FFEB9C",
            end_type="num",
            end_value=1,
            end_color="C6E0B4",
        ),
    )
    ch = BarChart()
    ch.type, ch.title, ch.style = "col", "Records passing all six rules", 10
    ch.y_axis.number_format, ch.y_axis.scaling.min, ch.y_axis.scaling.max = "0%", 0, 1
    ch.add_data(Reference(dash, min_col=4, min_row=13, max_row=16), titles_from_data=True)
    ch.set_categories(Reference(dash, min_col=1, min_row=14, max_row=16))
    ch.legend = None
    ch.dataLabels = DataLabelList()
    ch.dataLabels.showVal, ch.dataLabels.showSerName, ch.dataLabels.showCatName, ch.dataLabels.showLegendKey = (
        True,
        False,
        False,
        False,
    )
    ch.height, ch.width = 8, 14
    dash.add_chart(ch, "A18")
    dash.merge_cells("A35:F38")
    for r_ in range(35, 39):
        dash.row_dimensions[r_].height = 20
    dash["A35"].alignment = Alignment(wrap_text=True, vertical="top")
    dash["A35"], dash["A35"].font = (
        "Read this as: standardization repairs formatting (big jump); merging duplicates adds a smaller gain because fields blanked as unfixable can only be recovered when another record in the cluster has a valid value.",
        NOTE,
    )
    set_widths(dash, [20, 14, 14, 14, 14, 14])

    add_matcher_comparison(wb)
    all_rule = vs[vs.rule_id == "ALL"].iloc[0]
    add_executive_summary(
        wb,
        golden_all_pass=all_rule.golden_passed / all_rule.golden_total,
        raw_all_pass=all_rule.raw_passed / all_rule.raw_total,
    )
    wb.active = 0

    for ws in wb.worksheets:  # print-friendly: one page wide, landscape
        ws.page_setup.orientation = "landscape"
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, (1 if ws.title in ("Dashboard", "Executive Summary") else 0)
    REPORT_DIR.mkdir(exist_ok=True)
    wb.save(REPORT_PATH)
    log.info("saved %s", REPORT_PATH.name)


if __name__ == "__main__":
    main()
