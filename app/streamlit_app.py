"""Customer master data dashboard.

    pip install -r requirements-app.txt
    streamlit run app/streamlit_app.py

Reads outputs/ and data/raw/ (committed, or regenerate with `python scripts/run_pipeline.py`). The core pipeline does
not need Streamlit. All data is synthetic.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from customer_master import dashboard_data as dd  # noqa: E402
from customer_master import review_store as rs  # noqa: E402
from customer_master.impact import SCENARIO_DEFAULTS, scenario_grid, simulate_scenario  # noqa: E402

st.set_page_config(page_title="Customer master data dashboard", layout="wide")


def _chart(chart: alt.Chart, **_ignored) -> None:
    """Full-width Altair chart that works on old and new Streamlit (the `width` argument arrived after 1.50)."""
    try:
        st.altair_chart(chart, width="stretch")
    except TypeError:
        st.altair_chart(chart, use_container_width=True)


def _table(df: pd.DataFrame, **kwargs) -> None:
    """Full-width table; same old/new Streamlit compatibility as _chart."""
    try:
        st.dataframe(df, width="stretch", **kwargs)
    except TypeError:
        st.dataframe(df, use_container_width=True, **kwargs)


@st.cache_data(show_spinner="Loading pipeline outputs...")
def _data() -> dict:
    return dd.load_dashboard_data()


@st.cache_data(show_spinner=False)
def _quality(_raw: pd.DataFrame, _clean: pd.DataFrame) -> pd.DataFrame:
    return dd.quality_by_source_field(_raw, _clean)


def _bars(df: pd.DataFrame, x: str, y: str, color: str, fmt: str = "%", title: str | None = None) -> alt.Chart:
    """Grouped bar chart with side-by-side bars per `color` value."""
    axis = alt.Axis(format=fmt) if fmt else alt.Axis()
    return (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X(f"{x}:N", sort=None, title=None),
            xOffset=f"{color}:N",
            y=alt.Y(f"{y}:Q", axis=axis, title=title),
            color=f"{color}:N",
            tooltip=[x, color, alt.Tooltip(f"{y}:Q", format=".1%" if fmt == "%" else ",.3f")],
        )
    )


# ----------------------------------------------------------------------------- 1. executive dashboard
def page_executive(d: dict) -> None:
    st.header("Executive dashboard")
    st.caption(
        "Counts, golden records and data quality come from the easy original dataset. The F1 score comes from the "
        "hard held-out set, which is the credible accuracy number."
    )
    for col, k in zip(st.columns(5), dd.kpis(d)):
        col.metric(k["label"], k["value"], help=k["help"])

    left, right = st.columns(2)
    with left:
        st.subheader("Source records vs golden records")
        chart = (
            alt.Chart(dd.source_vs_golden(d))
            .mark_bar()
            .encode(x=alt.X("label:N", sort=None, title=None), y="records:Q", color="kind:N",
                    tooltip=["label", "records"])
        )  # fmt: skip
        _chart(chart)
    with right:
        st.subheader("Duplicate distribution")
        st.caption("Customers by how many source records were merged into them (1 = no duplicate found).")
        chart = (
            alt.Chart(dd.duplicate_distribution(d))
            .mark_bar()
            .encode(x=alt.X("source_records_per_customer:N", title="source records per customer", sort=None),
                    y="customers:Q", tooltip=["source_records_per_customer", "customers"])
        )  # fmt: skip
        _chart(chart)

    left, right = st.columns(2)
    with left:
        st.subheader("Data quality by source system")
        st.caption("Share of records passing all six validation rules.")
        rate = dd.all_rules_pass_rate(d["raw"], d["clean"])
        _chart(_bars(rate, "source_system", "pass_rate", "stage"))
    with right:
        st.subheader("Before and after cleaning")
        v = d["validation"][d["validation"].rule_id != "ALL"]
        long = v.melt(id_vars=["rule_id", "field"], value_vars=["raw_pass_rate", "std_pass_rate", "golden_pass_rate"],
                      var_name="stage", value_name="pass_rate")  # fmt: skip
        long["stage"] = long.stage.str.replace("_pass_rate", "", regex=False)
        _chart(_bars(long, "field", "pass_rate", "stage"))


# ----------------------------------------------------------------------------- 2. data quality
def page_quality(d: dict) -> None:
    st.header("Data quality analysis")
    q = _quality(d["raw"], d["clean"])
    all_systems = sorted(q.source_system.unique())
    systems = st.sidebar.multiselect("Source system", all_systems, default=all_systems)
    fields = st.sidebar.multiselect("Field", dd.FIELDS, default=dd.FIELDS)
    qf = q[q.source_system.isin(systems) & q.field.isin(fields)]
    if qf.empty:
        st.info("Select at least one source system and one field in the sidebar.")
        return

    st.subheader("Data quality before and after cleaning")
    agg = qf.groupby(["field", "stage"], as_index=False)[["passed", "records"]].sum()
    agg["pass_rate"] = agg.passed / agg.records
    _chart(_bars(agg, "field", "pass_rate", "stage", title="records passing the field's rule"))  # fmt: skip

    left, right = st.columns(2)
    with left:
        st.subheader("Missing values by column")
        miss = dd.missing_by_column(d["raw"])
        miss = miss[miss.source_system.isin(systems) & miss.field.isin(fields)]
        _chart(_bars(miss, "field", "missing_pct", "source_system", fmt="", title="% missing"))
    with right:
        st.subheader("Invalid and inconsistent values (raw)")
        raw = d["raw"][d["raw"].source_system.isin(systems)]
        _table(dd.issue_counts(raw), hide_index=True)
        st.caption("Counted on raw values before cleaning. A record can appear in more than one row.")

    st.subheader("Inconsistent city names")
    st.caption("Raw spellings that the standardizer maps to one canonical city, e.g. BPL and Bhopal MP to Bhopal.")
    mask = d["raw"].source_system.isin(systems)
    _table(dd.city_variants(d["raw"][mask], d["clean"][mask]), hide_index=True)

    st.subheader("Rule-level results")
    cols = ["rule_id", "field", "definition", "raw_pass_rate", "std_pass_rate", "golden_pass_rate"]
    _table(d["validation"][cols], hide_index=True)


# ----------------------------------------------------------------------------- 3. golden record explorer
def page_explorer(d: dict) -> None:
    st.header("Golden record explorer")
    query = st.text_input("Search a customer by name, phone, e-mail or record id", value="Riya Pillai")
    hits = dd.search_golden(d, query)
    if st.checkbox("Only customers built from 2+ source records", value=False):
        hits = hits[hits.n_source_records.astype(int) > 1]
    if hits.empty:
        st.info("No customer matches. Try part of a name, a phone number or an id such as CRM-00025.")
        return
    labels = {r.golden_id: f"{r.golden_id}: {r.full_name} ({r.n_source_records} source records)"
              for r in hits.itertuples()}  # fmt: skip
    gid = st.selectbox("Customer", list(labels), format_func=labels.get)
    info = dd.explain_golden(gid, d)

    st.subheader(f"{gid}: {info['golden'].full_name}")
    st.markdown("**1. Original source records**")
    _table(info["raw"], hide_index=True)
    st.markdown("**2. Standardized values** (blank = could not be repaired safely, never guessed)")
    _table(info["standardized"], hide_index=True)
    st.markdown("**3. Golden record: selected value, contributing source, and why**")
    cols = ["field", "golden_value", "from_source", "from_record", "why_selected"]
    _table(info["decisions"][cols], hide_index=True)
    if info["evidence"].empty:
        st.write("Single source record: nothing was merged.")
    else:
        st.markdown("**Why these records were merged** (1 = key agrees)")
        _table(info["evidence"], hide_index=True)


# ----------------------------------------------------------------------------- 4. matching evaluation
def page_evaluation(d: dict) -> None:
    st.header("Matching evaluation")
    st.caption(
        "Held-out = a hard dataset (seed 202) never used for tuning; the matcher configuration was frozen on the dev "
        "set first (see outputs/heldout_ledger.json)."
    )
    names = {"heldout": "Held-out (never tuned on)", "dev": "Development (tuned on)"}
    dataset = st.radio("Dataset", list(names), horizontal=True, format_func=names.get)
    table = dd.matcher_table(d, dataset)
    long = table.melt(id_vars="matcher", value_vars=["precision", "recall", "f1"], var_name="metric",
                      value_name="value")  # fmt: skip
    _chart(_bars(long, "matcher", "value", "metric"))
    fmt = {c: st.column_config.NumberColumn(format="%.3f") for c in ("precision", "recall", "f1")}
    _table(table, hide_index=True, column_config=fmt)

    st.subheader("What these metrics mean for the business")
    pick = st.selectbox("Matcher", list(table.matcher), index=len(table) - 1)
    row = table[table.matcher == pick].iloc[0]
    for sentence in dd.business_reading(row.precision, row.recall):
        st.markdown(f"- {sentence}")

    st.subheader("Development vs held-out")
    gap = dd.dev_vs_heldout(d)
    _chart(_bars(gap, "matcher", "gap", "metric", fmt="", title="held-out minus dev"))
    st.caption("Gaps near zero mean the dev numbers were not over-fitted.")

    st.subheader("Precision-recall trade-off (dev set, as the decision threshold changes)")
    chart = (
        alt.Chart(d["pr_curve"])
        .mark_line(point=True)
        .encode(x=alt.X("recall:Q", axis=alt.Axis(format="%")), y=alt.Y("precision:Q", axis=alt.Axis(format="%")),
                color="matcher:N", tooltip=["matcher", "threshold", alt.Tooltip("precision:Q", format=".1%"),
                                            alt.Tooltip("recall:Q", format=".1%")])
    )  # fmt: skip
    _chart(chart)

    st.subheader("Error analysis: real examples from the dev set")
    for e in dd.error_examples(d):
        with st.expander(f"{e['kind']}: {e['summary']}"):
            st.write(f"Match weight **{e['score']}**, system decision **{e['decision']}**, "
                     f"truly the same customer: **{e['same_customer']}**")  # fmt: skip
            _table(e["records"], hide_index=True)


# ----------------------------------------------------------------------------- 5. human review
def page_review(d: dict) -> None:
    st.header("Human review queue")
    st.caption(
        "Pairs the matcher is unsure about. Verdicts are saved to a local SQLite audit trail and never change the "
        "evaluation data. This is a demo store: on a hosted deployment it resets when the app restarts."
    )
    reviewer = st.sidebar.text_input("Reviewer name", value="analyst")
    con = rs.connect()
    try:
        queue = rs.pending_queue(d["review_queue"], con)
        summary = rs.decision_summary(con)
        c = st.columns(4)
        c[0].metric("Pending pairs", f"{len(queue):,}")
        c[1].metric("Merged", summary["merge"])
        c[2].metric("Kept separate", summary["keep_separate"])
        c[3].metric("Needs investigation", summary["needs_investigation"])

        if queue.empty:
            st.success("No pending pairs.")
        else:
            pos = st.number_input("Queue position (1 = highest priority)", 1, len(queue), 1)
            row = queue.iloc[int(pos) - 1]
            st.markdown(
                f"**{row.a_id}** vs **{row.b_id}**: match score {row.score}, "
                f"match probability {row.match_probability:.0%}, queued because *{row.reason_in_queue}*"
            )
            expl = rs.explain_pair(row)
            _table(expl, hide_index=True)
            st.info(rs.risk_note(expl))
            with st.form("verdict", clear_on_submit=True):
                choice = st.radio("Decision", list(rs.DECISIONS), horizontal=True,
                                  format_func=lambda x: x.replace("_", " ").capitalize())  # fmt: skip
                reason = st.text_area("Reason (required)")
                if st.form_submit_button("Save decision"):
                    try:
                        rs.record_decision(con, row.a_id, row.b_id, choice, reason, reviewer, row.score,
                                           row.match_probability, int(row["rank"]))  # fmt: skip
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
        st.subheader("Audit trail")
        trail = rs.audit_trail(con)
        _table(trail, hide_index=True)
        if not trail.empty:
            st.download_button("Download audit trail (CSV)", trail.to_csv(index=False), "review_audit_trail.csv")
    finally:
        con.close()


# ----------------------------------------------------------------------------- 6. impact simulator
def page_impact(d: dict) -> None:
    st.header("Business impact simulator")
    st.warning("Hypothetical scenario estimates, not verified savings. Change the assumptions to see how they matter.")
    s = SCENARIO_DEFAULTS
    c1, c2 = st.columns(2)
    with c1:
        records = st.number_input("Customer records", 1_000, 10_000_000, int(s["records"]), step=10_000)
        dup = st.slider("Duplicate rate", 0.0, 0.5, s["duplicate_rate"], 0.01, format="%.2f")
        corr = st.number_input("Cost of fixing one duplicate by hand (INR)", 0.0, 10_000.0, s["correction_cost_inr"])
        wrong = st.number_input("Cost of one incorrect merge (INR)", 0.0, 100_000.0, s["wrong_merge_cost_inr"])
    with c2:
        auto = st.slider("Duplicates merged automatically (recall)", 0.0, 1.0, s["auto_resolve_rate"], 0.01)
        fm = st.slider("Automatic merges that are wrong", 0.0, 0.5, s["false_merge_rate"], 0.005, format="%.3f")
        amb = st.slider("Duplicates sent to human review", 0.0, 1.0, s["ambiguous_share"], 0.01)
        rev = st.number_input("Cost per manual review (INR)", 0.0, 10_000.0, s["review_cost_inr"])
        acc = st.slider("Reviewer accuracy", 0.5, 1.0, s["reviewer_accuracy"], 0.01)
    params = dict(records=records, duplicate_rate=dup, correction_cost_inr=corr, auto_resolve_rate=auto,
                  false_merge_rate=fm, ambiguous_share=amb, review_cost_inr=rev, reviewer_accuracy=acc,
                  wrong_merge_cost_inr=wrong)  # fmt: skip
    r = simulate_scenario(**params)
    m = st.columns(4)
    m[0].metric("Duplicates", f"{r['duplicates']:,}")
    m[1].metric("All fixed by hand (INR)", f"{r['baseline_manual_cost_inr']:,}")
    m[2].metric("With pipeline (INR)", f"{r['pipeline_cost_inr']:,}")
    m[3].metric("Scenario saving (INR)", f"{r['scenario_saving_inr']:,}")
    parts = pd.DataFrame(
        {
            "scenario": ["All by hand", "With pipeline", "With pipeline", "With pipeline"],
            "component": ["manual fixing", "manual fixing", "human review", "wrong merges"],
            "cost_inr": [r["baseline_manual_cost_inr"], r["manual_fix_cost_inr"], r["review_cost_inr"],
                         r["wrong_merge_cost_inr"]],
        }
    )  # fmt: skip
    chart = alt.Chart(parts).mark_bar().encode(x="scenario:N", y="cost_inr:Q", color="component:N",
                                               tooltip=["scenario", "component", "cost_inr"])  # fmt: skip
    _chart(chart)
    if r["break_even_wrong_merge_cost_inr"] is not None:
        st.caption(f"Break-even: the pipeline stays cheaper than fixing everything by hand until one wrong merge "
                   f"costs about INR {r['break_even_wrong_merge_cost_inr']:,.0f}.")  # fmt: skip
    st.subheader("Duplicate rate vs share sent to review")
    grid = pd.DataFrame(scenario_grid(params, [0.05, 0.10, 0.15, 0.20, 0.30], [0.0, 0.05, 0.10, 0.20, 0.30]))
    heat = alt.Chart(grid).mark_rect().encode(
        x=alt.X("ambiguous_share:O", title="share sent to review"), y=alt.Y("duplicate_rate:O", title="duplicate rate"),
        color=alt.Color("scenario_saving_inr:Q", title="saving (INR)"),
        tooltip=["duplicate_rate", "ambiguous_share", "scenario_saving_inr"],
    )  # fmt: skip
    _chart(heat)
    with st.expander("Formulas"):
        st.code(simulate_scenario.__doc__)


PAGES = {
    "Executive dashboard": page_executive,
    "Data quality": page_quality,
    "Golden record explorer": page_explorer,
    "Matching evaluation": page_evaluation,
    "Human review": page_review,
    "Impact simulator": page_impact,
}

st.sidebar.title("Customer master data")
section = st.sidebar.radio("Section", list(PAGES))
st.sidebar.caption("All data is synthetic.")
try:
    PAGES[section](_data())
except FileNotFoundError as err:
    st.error(str(err))
