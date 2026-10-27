# reports/

`customer_data_quality_report.xlsx` is built by step 6 from `outputs/*.csv` and `outputs/metrics.json`. Counts are blue input cells; rates, F1 and totals are live formulas. Regenerate with `python scripts/run_pipeline.py`.

Sheet order: Executive Summary (held-out matching results first), then the easy-data sheets (Dashboard, Data Quality, Matching), Matcher Comparison, and supporting sheets.
