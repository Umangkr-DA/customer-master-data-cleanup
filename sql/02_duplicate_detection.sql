-- =============================================================================
-- 02_duplicate_detection.sql
-- Purpose : Candidate generation ("blocking") on STANDARDIZED data. No decision is made here -
--           this step only lists record pairs that share at least one exact key.
-- Input   : clean_customers (standardized; unfixable values are NULL)
-- Output  : candidate_flags (records that share a key with another record),
--           pair_candidates (record pairs + which exact keys they share)
-- Exact keys, in order of strength:
--           phone | email | full_name + pincode
-- NULL keys never match: a blank phone is not evidence of anything.
-- =============================================================================

DROP TABLE IF EXISTS candidate_flags;
CREATE TABLE candidate_flags AS
SELECT record_id,
       -- window COUNT: how many records share this record's phone / email?
       -- (NULL keys are partitioned per record so blanks never group together)
       COUNT(*) OVER (PARTITION BY COALESCE(phone, 'nophone:' || record_id)) AS phone_group_size,
       COUNT(*) OVER (PARTITION BY COALESCE(email, 'noemail:' || record_id)) AS email_group_size
FROM clean_customers;

DROP TABLE IF EXISTS pair_candidates;
CREATE TABLE pair_candidates AS
WITH shared_keys AS (
    SELECT a.record_id AS a_id, b.record_id AS b_id, 1 AS phone_match, 0 AS email_match, 0 AS name_pin_match
    FROM clean_customers a
    JOIN clean_customers b ON a.phone = b.phone AND a.record_id < b.record_id

    UNION ALL
    SELECT a.record_id, b.record_id, 0, 1, 0
    FROM clean_customers a
    JOIN clean_customers b ON a.email = b.email AND a.record_id < b.record_id

    UNION ALL
    -- secondary key for people whose phone AND email differ or are blank:
    -- identical standardized full name + identical full 6-digit pincode (both present)
    SELECT a.record_id, b.record_id, 0, 0, 1
    FROM clean_customers a
    JOIN clean_customers b ON a.full_name = b.full_name AND a.pincode = b.pincode AND a.record_id < b.record_id
)
SELECT a_id, b_id,
       MAX(phone_match)    AS phone_match,
       MAX(email_match)    AS email_match,
       MAX(name_pin_match) AS name_pin_match
FROM shared_keys
GROUP BY a_id, b_id;          -- one row per pair even if several keys agree
