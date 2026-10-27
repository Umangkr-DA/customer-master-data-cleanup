# Case studies: where the best matcher fails

Matcher: the frozen Fellegi-Sunter configuration (`fellegi-sunter`, config hash in `outputs/heldout_ledger.json`).
Data: **dev only** (`data/dev`, seed 101); the held-out set was not inspected for this analysis.
Every number below is produced by `python scripts/case_studies.py` and stored in
[`outputs/case_study_evidence.json`](../outputs/case_study_evidence.json). Scores are Fellegi-Sunter match weights
(log2 likelihood ratio); the frozen thresholds are merge >= 4.87, review >= -0.27.

Where the 387 missed pairs on dev went: 136 were never proposed by blocking, 238 were scored into the review band
(never auto-merged), 13 scored below the review band.

---

## Case 1 - Missed duplicate: identical phone and email, but one name is blank

| | CRM-01441 | SALES-00790 |
|---|---|---|
| raw name | `Poojaa Arora` | `customer` (placeholder) |
| standardized name | Poojaa Arora | *(blank)* |
| phone | 8640833634 | 8640833634 |
| email | pooja.arora9403@hotmail.com | pooja.arora9403@hotmail.com |
| pincode / signup date | 226034 / 2020-03-24 | 226034 / 2020-03-24 |

* **What happened:** the pair is the same customer. Its match weight is **51.1**, far above the merge threshold of
  4.87, but the decision is **review**, not merge.
* **Root cause:** the frozen name-missing policy is `review`: a pair with a blank name is never auto-merged, in line
  with the project's "blank is better than wrong" principle. 48 of the 49 dev pairs that share an identical phone *and*
  email yet were not merged have a blank name.
* **Evidence the policy is not simply wrong:** among all candidate pairs on dev with identical phone, identical email
  and a blank name, **61 pairs, of which 48 (79%) are the same customer**. Auto-merging them would have added 13
  false merges (pairs of different people) to gain 48 true pairs.
* **Fix / decision:** left as is in the frozen matcher. These pairs are exactly what the human-review queue
  (Phase 4, `outputs/review_queue.csv`) is for, and they rank near the top because their scores are so high. The
  `phone+email` policy variant that would merge them was part of the dev grid and did not win on dev F1
  (`outputs/tuning_dev.csv`).

## Case 2 - False merge: one digit off, names reversed

| | SALES-00919 | WEB-01748 |
|---|---|---|
| name | Salman Sharma | Sharma Salman |
| phone | 6699088705 | 6699088704 |
| email | salman78.sharma@hotmail.com | salman.sharma2080@gmail.com |
| city / pincode | Mumbai / 400027 | Mumbai / 400027 |
| true entity | E01186 | E02533 (different person) |

* **What happened:** merged with a weight of 9.12 (merge threshold 4.87). Evidence string:
  `first=3 last=3 phone=1 email=1 city=1 pincode=1`, so the phone differs by one digit, the e-mail local parts are
  similar, and the pair shares city and pincode.
* **Root cause:** the generator plants typo'd phones for true duplicates, so the model learns that a one-digit
  difference is strong evidence of a match. It is, except when two different people with a very common name live in the
  same pincode and their phones differ only in the last digit. Nothing in the fields separates this case from a
  genuine typo.
* **Scale on dev:** 10 pairs were falsely merged; 6 of them share the same phone, only 2 have identical names.
  A second example in that group is `CRM-00131` / `SALES-01324` (Rajesh Shah, same phone and pincode, different
  e-mail), a planted "namesake in the same household" case with weight 21.7.
* **Fix / decision:** not fixed. Raising the merge threshold enough to exclude these pairs also removes true matches
  (the dev precision floor of 0.97 was chosen to balance this; see the PR curve in `docs/pr_curve.png`). The realistic
  mitigation is human review of borderline merges (Phase 4), not more rules. On held-out data precision was 0.965 against 0.983 on dev, i.e. the frozen
  threshold generalised slightly worse than the dev calibration suggested.

## Case 3 - Survivorship surprise: the "longest name" rule chose a typo

Cluster `G00072` (dev, correctly merged, one true customer, four records):
names `Pranav Mauhotra`, `Pranav Malhotra`, `P Malhotra`, `Pranav Malhotra`.

* **What happened:** the golden record took **`Pranav Mauhotra`**. The longest-name rule ranks by length, and
  the typo has the same length as the correct spelling, so the tie fell to source priority (CRM first), and the CRM record
  carried the typo.
* **Root cause:** length is a poor proxy for correctness once names contain typos. The rule was designed for initials
  versus full names (`R Sharma` vs `Rahul Sharma`), not for noisy full names.
* **Evidence of scale:** among the 379 correctly merged dev clusters whose members have conflicting names, a one-off
  variant won over a spelling seen at least twice in **24** clusters under the previous rule (replayed on the same
  clusters in `case_study_evidence.json`).
* **Fix (applied):** `sql/04_survivorship.sql` now ranks names by (1) a full first name over an initial, (2) the
  spelling shared by most records of the cluster, (3) length, (4) source priority. Under the new rule the count drops
  from **24 to 1**. The remaining case is `R Pillai`, `R Pillai`, `Rahl Pillai`: an initial never beats a full first
  name, even a misspelled one, which is a deliberate choice. A unit test covers both behaviours.
* **Not fixed:** 56 golden names on dev are the *reversed order* of another member of the cluster (`Yadav Amit` vs
  `Amit Yadav`). With one record of each order there is no majority, and deciding the order needs a first-name
  dictionary. The tie falls to source priority.
* **Caveat:** the proxy (agreement with the repeated spelling) is the only available check, because the answer key
  stores entity ids and not true names. The main pipeline's headline numbers use data with no name typos and are
  therefore unaffected.

## Case 4 (additional) - Duplicates that cannot be found: both phone and email changed

`CRM-00240` "Aditta Garg" and `SALES-01215` "Garg Aditya": different phone, different e-mail domain, name typo plus
reversed order, same city and pincode. Weight -1.75, below the review band.

* **Root cause:** 136 of the 387 dev misses were never candidates, and in all 136 the phone and the e-mail both differ
  or are blank. What is left (name, city, pincode) is shared by many different people, so blocking on it would
  create large candidate sets with almost no true matches. Pair completeness of the blocking scheme on dev is
  0.896, which caps pair recall at that value for every scorer.
* **Fix / decision:** left unfixed on purpose. The only remedy is more evidence (an address line, a date of birth), which
  this schema does not have.
