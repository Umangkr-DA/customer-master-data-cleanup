# Data dictionary

## Raw source files (`data/raw/*.csv`)
| Column | Meaning |
|---|---|
| record_id | Unique source record identifier (prefix = system, e.g. CRM-00001); must be unique across systems |
| source_system | CRM, Web or Sales |
| full_name, phone, email, city, pincode, signup_date | Raw text, may contain formatting noise, placeholders or blanks |

## Golden records (`outputs/golden_records.csv`)
| Column | Meaning |
|---|---|
| golden_id | Customer identifier (G00001...), assigned by sorted cluster label; stable for a given input |
| full_name, phone, email, city, pincode, signup_date | Surviving standardized values; NULL if no record in the cluster had a valid value |
| name_source, phone_source, email_source, address_source, date_source | Source system the surviving value came from |
| n_source_records | Number of source records merged into this golden record |
| source_systems | Distinct source systems in the cluster |
| merged_record_ids | Source `record_id`s in the cluster (lineage back to `data/raw`) |
| match_rules | Distinct rules that formed the cluster (blank for singletons) |
| lowest_match_confidence | Medium if any accepted pair in the cluster was Medium, else High |

## Match pairs (`outputs/match_pairs.csv`)
a_id, b_id (source record ids, a_id < b_id); phone_match / email_match / name_pin_match (1 = exact key agrees); name_status (1 same person, -1 a name missing); keys_agreeing; match_rule; match_confidence (High = 2+ keys, Medium = 1 key). Confidence is a rule-based category, not a calibrated probability.

## Conflict resolution
Per field, a non-NULL value beats NULL. Name: longest, then CRM > Sales > Web. Phone: Sales > CRM > Web. Email: Web > CRM > Sales. City+pincode: most complete pair from one record, then CRM > Sales > Web. Signup date: earliest. Final ties: lowest record_id. Source priorities are documented assumptions, not learned from data; the dataset has no source-quality information.
