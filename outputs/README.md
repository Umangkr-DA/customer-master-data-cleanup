# outputs/

Files marked *pipeline* are **regenerated on every run** of `python scripts/run_pipeline.py` (easy dataset, seed 42). The rest are written by the experiment scripts named below from the dev and held-out data. Do not edit any of them by hand.

| File | Content |
|---|---|
| `metrics.json` | Single source of truth for every headline number (README and Excel read it) |
| `golden_records.csv` | One row per customer, with `*_source` lineage columns and `merged_record_ids` |
| `match_pairs.csv` | Accepted record pairs with match rule and High/Medium confidence |
| `match_rule_summary.csv`, `cluster_size_distribution.csv` | Matching summaries |
| `evaluation_summary.csv`, `missed_pairs_analysis.csv` | Pair-level evaluation and error analysis |
| `validation_summary.csv`, `quality_by_source.csv` | Data-quality scorecard (raw, standardized, golden) |
| `data_profile.csv`, `source_profile.csv`, `cleaning_log.csv` | Profiling and cleaning outcome counts |

## Upgrade outputs (hard data, matchers, scale, business)

| File | Written by | Content |
|---|---|---|
| `headline_metrics.json` | pipeline (read-only over the files below) | Numbers quoted in the README; checked by `run_pipeline.py` |
| `matcher_comparison.csv` | `run_matcher_comparison.py` | Matcher x {dev, held-out}: P/R/F1, B-cubed, purity, candidate pairs, runtime |
| `heldout_ledger.json` | `run_matcher_comparison.py` | Frozen configuration hash, code fingerprint and the single held-out result per configuration |
| `tuning_dev.csv`, `fs_parameters_dev.json` | `run_matcher_comparison.py` | Dev-only tuning grid and fitted Fellegi-Sunter parameters |
| `febrl_comparison.csv`, `febrl_tuning_dataset1.csv` | `run_matcher_comparison.py` | Public Febrl benchmark |
| `baseline_hard.csv` | `baseline_hard.py` | Original exact-key rules on the hard data |
| `multiseed_*.csv` | `run_phase3.py` | 20-seed results per matcher with 95% intervals |
| `pr_curve_dev.csv` | `run_phase3.py` | Precision-recall over the score threshold |
| `case_study_evidence.json` | `case_studies.py` | Evidence behind `docs/case_studies.md` |
| `review_queue.csv`, `review_simulation*.csv`, `review_ranking_variants_dev.csv`, `review_queue_composition_dev.csv` | `run_phase4.py` | Review queue and the simulated reviewer |
| `incremental_check.json` | `run_incremental.py` | Incremental vs full rebuild |
| `scale_benchmark.csv/.json` | `benchmark_scale.py` | Runtime, memory and quality at 5k, 50k, 500k |
| `duckdb_verification.json` | `verify_duckdb.py` | SQLite vs DuckDB, cell by cell |
| `impact_estimate.json`, `impact_sensitivity.csv` | `run_phase6.py` | Business estimate from `config/assumptions.json` |
| `dq_history.csv` | `monitor_quality.py` | Six rule pass rates per run (history) |
| `coverage.txt` | `make test` | Test coverage summary |

