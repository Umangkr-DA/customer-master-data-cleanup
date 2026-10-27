# data/

All data is **synthetic** (or, for the optional benchmark, a public synthetic benchmark). No real people are represented.

| Folder | What | Seed | Used for |
|---|---|---|---|
| `raw/` + `ground_truth/` | Original easy dataset, 5,219 records (`generate_data.py`) | 42 | The main pipeline run and its Excel report |
| `dev/` | **Hard mode**, 5,171 records (`generate_hard.py`) | 101 | Designing and tuning matchers. The only data used for decisions |
| `heldout/` | **Hard mode**, 5,174 records, same generator | 202 | Evaluated **once per frozen matcher configuration**. Never used for tuning |
| `benchmarks/febrl/` | Public Febrl record-linkage benchmark (git-ignored, fetched on demand) | n/a | External sanity check (`scripts/fetch_febrl.py`) |
| `sample/` | Before/after cleaning examples written by the standardize step | | README |
| `processed/` (git-ignored) | SQLite database and cleaned CSV created at run time | | |

Each of `dev/` and `heldout/` contains `crm.csv`, `web.csv`, `sales.csv`, `answer_key.csv` and
`generation_config.json` (every noise rate used). Regenerate or verify with
`python scripts/make_datasets.py [--check]`.

## The split, and what it does and does not prove

* The generator parameters in `generate_hard.py` were fixed **before** any matcher ran on them and were not changed
  afterwards.
* Matching rules, thresholds and EM settings are chosen on `dev/` only. A configuration is then frozen (its hash is
  written to `outputs/heldout_ledger.json` with the git commit) and evaluated on `heldout/` once.
  `load_dataset("heldout")` raises unless the frozen-config evaluator asks for it.
* **Limit:** dev and held-out come from the same generator, so held-out measures generalisation to a new sample, not to
  a different noise model. The 20-seed runs measure the same thing with intervals. The Febrl benchmark is the only
  evaluation whose noise was not written by this project's author.

## Answer keys

`answer_key.csv` (record_id → true entity_id) is read only by evaluation code (`evaluation.py`, `experiments.py`,
`validation.py`, `heldout.py`). `tests/test_no_leakage.py` fails if any matcher, standardizer or survivorship module
references it.

## Hard-mode noise (see `generation_config.json` for the exact rates)

Phone typos (transposed, substituted, dropped digits); nickname and spelling variants (Mohammed / Mohd / Muhammad);
name typos; swapped first/last order; initials; common names drawn from a Zipf-like distribution; narrow pincode range
so many different people share name + city + pincode; households sharing a phone and sometimes an email; 8 "namesake"
pairs (same name, phone and pincode, different people); recycled phone numbers; blank name/phone/email rates of
10-12%; duplicates where both phone and email changed (15%) or the customer moved (10%).
