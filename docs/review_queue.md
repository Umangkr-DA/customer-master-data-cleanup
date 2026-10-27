# Human review queue

`python scripts/run_phase4.py` writes [`outputs/review_queue.csv`](../outputs/review_queue.csv) for the dev data with the
frozen Fellegi-Sunter matcher. It never reads the answer key; only the **simulation** does.

## What is in the queue

Every candidate pair in the review band, plus every candidate pair with a blank name, minus pairs whose records are
already in the same auto-merged cluster. Each row gives both records' values, the score, which fields agree, disagree
or are missing, the current cluster sizes, a match probability and an expected value (EV), sorted by EV.

* **match probability** = Fellegi-Sunter weight converted with a prior estimated by EM on the candidate pairs, separately
  for pairs whose names agree, are blank, or conflict. The prior is label-free. It is an unsupervised estimate and is
  **not calibrated**: conflicting-name pairs are scored on phone, e-mail and address only, so many of them look
  near-certain.
* **EV** = expected number of correct pair decisions gained by judging the pair (`q` for a pair not merged yet,
  `1 - q` for an auto-merged blank-name pair).

## Which ranking, and why (chosen on dev)

[`outputs/review_ranking_variants_dev.csv`](../outputs/review_ranking_variants_dev.csv) compares five rankings with a
simulated perfect reviewer, and the variant with the highest recall after 250 reviews was chosen on dev:

* Weighting EV by the size of the two clusters (what a true merge would recover) looked principled but ranked
  **worse**: only 13% of its top 100 were true matches, because pairs joining two large auto-merged clusters are mostly
  mistakes (common names).
* Per-stratum priors fix part of the problem (47% of the top 100 true) but not the main one. Of the 930 queued pairs,
  510 have conflicting names and only 9 of those are true matches (1.8%), against 47% to 71% for the rest
  ([`outputs/review_queue_composition_dev.csv`](../outputs/review_queue_composition_dev.csv)).
* **`conflicts_last`** (chosen) lists every conflicting-name pair after all others. It is an ordinal rule, not a tuned
  constant: the matcher already treats a name conflict as a veto. 87% of its top 100 are true matches, against 26% for
  a random order.
* A first version of the ranking, which pooled all pairs under one prior, was no better than random order.

## Simulation (the answer key plays the reviewer; this is NOT real review)

`outputs/review_simulation.csv` (dev) and `outputs/review_simulation_seeds_summary.csv` (20 unseen seeds, mean and 95%
interval); chart: [`docs/review_simulation.png`](review_simulation.png). The ranking was chosen on dev; the seed runs
are fresh data.

* A perfect reviewer, queue order: pair recall rises from the matcher's 0.735 to 0.870 after 250 reviews, 0.913 after
  500 and 0.917 after the whole queue of 930 pairs, with precision not falling. Random order reaches 0.796 after 250
  reviews. On 20 unseen seeds the mean recall is 0.868 after 250 reviews (95% interval 0.861 to 0.874).
* A reviewer who is right 95% of the time: recall is similar, but precision falls from 0.983 to 0.941 over the full
  queue and over-merged clusters rise from 8 to 42, because most queued pairs are non-matches and every wrong "match"
  verdict merges two customers. Review has a cost when reviewers are imperfect: show them the evidence columns, and
  consider a second opinion for "match" verdicts on conflicting names.
* The recall ceiling (about 0.92) is set by blocking: pairs that share neither phone, e-mail, nor a usable name block
  never enter the queue.
