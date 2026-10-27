-- =============================================================================
-- 05_validation.sql
-- Purpose : Data-quality scorecard. Pass counts for the six rules at three stages.
-- Input   : val_raw / val_clean / val_golden (one 0/1 flag per rule per record, written by
--           src/customer_master/validation.py using the rules in src/customer_master/common.py),
--           rule_ref(rule_id, field, definition)
-- Output  : validation_summary (one row per rule + an ALL row)
-- Notes   : "ALL" = a record passes ALL six rules. A blank value FAILS its rule, so values left
--           blank on purpose (never guessed) are counted as failures, not hidden.
--           raw/standardized are measured over source records; golden over golden records.
-- =============================================================================

DROP TABLE IF EXISTS validation_summary;
CREATE TABLE validation_summary AS
WITH long_form AS (
    SELECT 'raw' AS stage, 'R1' AS rule_id, SUM(R1) AS passed, COUNT(*) AS total FROM val_raw
    UNION ALL
    SELECT 'raw' AS stage, 'R2' AS rule_id, SUM(R2) AS passed, COUNT(*) AS total FROM val_raw
    UNION ALL
    SELECT 'raw' AS stage, 'R3' AS rule_id, SUM(R3) AS passed, COUNT(*) AS total FROM val_raw
    UNION ALL
    SELECT 'raw' AS stage, 'R4' AS rule_id, SUM(R4) AS passed, COUNT(*) AS total FROM val_raw
    UNION ALL
    SELECT 'raw' AS stage, 'R5' AS rule_id, SUM(R5) AS passed, COUNT(*) AS total FROM val_raw
    UNION ALL
    SELECT 'raw' AS stage, 'R6' AS rule_id, SUM(R6) AS passed, COUNT(*) AS total FROM val_raw
    UNION ALL
    SELECT 'raw' AS stage, 'ALL' AS rule_id, SUM(all_pass) AS passed, COUNT(*) AS total FROM val_raw
    UNION ALL
    SELECT 'standardized' AS stage, 'R1' AS rule_id, SUM(R1) AS passed, COUNT(*) AS total FROM val_clean
    UNION ALL
    SELECT 'standardized' AS stage, 'R2' AS rule_id, SUM(R2) AS passed, COUNT(*) AS total FROM val_clean
    UNION ALL
    SELECT 'standardized' AS stage, 'R3' AS rule_id, SUM(R3) AS passed, COUNT(*) AS total FROM val_clean
    UNION ALL
    SELECT 'standardized' AS stage, 'R4' AS rule_id, SUM(R4) AS passed, COUNT(*) AS total FROM val_clean
    UNION ALL
    SELECT 'standardized' AS stage, 'R5' AS rule_id, SUM(R5) AS passed, COUNT(*) AS total FROM val_clean
    UNION ALL
    SELECT 'standardized' AS stage, 'R6' AS rule_id, SUM(R6) AS passed, COUNT(*) AS total FROM val_clean
    UNION ALL
    SELECT 'standardized' AS stage, 'ALL' AS rule_id, SUM(all_pass) AS passed, COUNT(*) AS total FROM val_clean
    UNION ALL
    SELECT 'golden' AS stage, 'R1' AS rule_id, SUM(R1) AS passed, COUNT(*) AS total FROM val_golden
    UNION ALL
    SELECT 'golden' AS stage, 'R2' AS rule_id, SUM(R2) AS passed, COUNT(*) AS total FROM val_golden
    UNION ALL
    SELECT 'golden' AS stage, 'R3' AS rule_id, SUM(R3) AS passed, COUNT(*) AS total FROM val_golden
    UNION ALL
    SELECT 'golden' AS stage, 'R4' AS rule_id, SUM(R4) AS passed, COUNT(*) AS total FROM val_golden
    UNION ALL
    SELECT 'golden' AS stage, 'R5' AS rule_id, SUM(R5) AS passed, COUNT(*) AS total FROM val_golden
    UNION ALL
    SELECT 'golden' AS stage, 'R6' AS rule_id, SUM(R6) AS passed, COUNT(*) AS total FROM val_golden
    UNION ALL
    SELECT 'golden' AS stage, 'ALL' AS rule_id, SUM(all_pass) AS passed, COUNT(*) AS total FROM val_golden
),
pivoted AS (                      -- one row per rule, one column per stage (conditional aggregation)
    SELECT rule_id,
           MAX(CASE WHEN stage = 'raw'          THEN total  END) AS raw_total,
           MAX(CASE WHEN stage = 'raw'          THEN passed END) AS raw_passed,
           MAX(CASE WHEN stage = 'standardized' THEN total  END) AS std_total,
           MAX(CASE WHEN stage = 'standardized' THEN passed END) AS std_passed,
           MAX(CASE WHEN stage = 'golden'       THEN total  END) AS golden_total,
           MAX(CASE WHEN stage = 'golden'       THEN passed END) AS golden_passed
    FROM long_form
    GROUP BY rule_id
)
SELECT p.rule_id,
       COALESCE(r.field, 'All 6 rules')                            AS field,
       COALESCE(r.definition, 'Record passes every rule')          AS definition,
       p.raw_total, p.raw_passed, p.raw_total - p.raw_passed       AS raw_failed,
       ROUND(1.0 * p.raw_passed / p.raw_total, 4)                  AS raw_pass_rate,
       p.std_total, p.std_passed, p.std_total - p.std_passed       AS std_failed,
       ROUND(1.0 * p.std_passed / p.std_total, 4)                  AS std_pass_rate,
       p.golden_total, p.golden_passed, p.golden_total - p.golden_passed AS golden_failed,
       ROUND(1.0 * p.golden_passed / p.golden_total, 4)            AS golden_pass_rate,
       -- improvement in percentage points
       ROUND(100.0 * (1.0 * p.std_passed / p.std_total - 1.0 * p.raw_passed / p.raw_total), 1)       AS raw_to_std_pp,
       ROUND(100.0 * (1.0 * p.golden_passed / p.golden_total - 1.0 * p.raw_passed / p.raw_total), 1) AS raw_to_golden_pp
FROM pivoted p
LEFT JOIN rule_ref r ON r.rule_id = p.rule_id
ORDER BY CASE WHEN p.rule_id = 'ALL' THEN 1 ELSE 0 END, p.rule_id;
