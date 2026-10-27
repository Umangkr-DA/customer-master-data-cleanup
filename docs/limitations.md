# Limitations

What this project now addresses, and what it does not.

## Addressed
- **Optimistic, self-designed evaluation.** Hard-mode dev and held-out sets, a frozen-config ledger, 20-seed intervals, cluster metrics, and a public Febrl benchmark.
- **Exact-key matching only.** Fuzzy rules and Fellegi-Sunter scoring with blocking, compared on the same data.
- **No review path.** A ranked review queue with a simulated reviewer.
- **Single full rebuild.** Incremental matching, tested against a full rebuild.
- **SQLite only.** The SQL pipeline is ported to DuckDB and checked cell by cell (`outputs/duckdb_verification.json`). No other engine was tested.

## Remaining
- **Synthetic data.** Dev, held-out and the 20 seeds share one generator, so they measure generalisation to a new sample, not to a different noise model. Febrl is the only data whose noise this project did not write.
- **Held-out is single-use per configuration.** After a result is seen, any change needs a new held-out set or seed.
- **Scale.** Beyond about 50k records precision degrades (97.7% at 5k and 93.9% at 50k with per-file EM). At 500k, frozen weights alone reach 43.9% precision; the prior-odds correction (thresholds raised by log2(N / 50k)) lifts that to 92.6% but recall falls to 61.8%. One run per size, one benchmark dataset, no intervals, and not tested on a differently generated large file. The standard matcher fails with a `MemoryError` under the benchmark's 6 GB cap (`outputs/scale_benchmark.csv`).
- **Review simulation uses a perfect reviewer** for the 20-seed curve; a 95%-accurate reviewer is simulated on dev only. Real reviewers disagree with each other.
- **Match probabilities are unsupervised and not calibrated.**
- **Business estimate rests on placeholder unit costs** (`config/assumptions.json`). It is an illustration.
- **Legacy pipeline.** The SQL pipeline and Excel report (5,219 easy records) still use exact-key rules with a name guard; its 100% precision is easy on that data.
- **Validation denominators differ** between raw/standardized (source records) and golden (golden records); the like-for-like figure is in `outputs/metrics.json`.
- **Survivorship priorities are assumptions**, not learned.
- **Pair-level recall over-weights 3-record customers.**
- **Excel counts are pipeline inputs** (blue cells); rates and totals are formulas. The Executive Summary and Matcher Comparison sheets show the held-out results; the other sheets and the Dashboard and Data Quality screenshots describe the easy-data pipeline.
- **Real customer data** would need access controls and privacy review that this project does not implement.
