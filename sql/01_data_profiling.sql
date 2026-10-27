-- =============================================================================
-- 01_data_profiling.sql
-- Purpose : Profile the RAW customer table before touching anything.
-- Input   : raw_customers(record_id, source_system, full_name, phone, email, city, pincode, signup_date)
-- Output  : profile_summary (one row per field), source_profile (one row per source system)
-- =============================================================================

DROP TABLE IF EXISTS profile_summary;
CREATE TABLE profile_summary AS
WITH field_values AS (            -- unpivot: one row per (record, field)
    SELECT record_id, 'full_name'   AS field, full_name   AS value FROM raw_customers
    UNION ALL SELECT record_id, 'phone',       phone       FROM raw_customers
    UNION ALL SELECT record_id, 'email',       email       FROM raw_customers
    UNION ALL SELECT record_id, 'city',        city        FROM raw_customers
    UNION ALL SELECT record_id, 'pincode',     pincode     FROM raw_customers
    UNION ALL SELECT record_id, 'signup_date', signup_date FROM raw_customers
),
flagged AS (
    SELECT field,
           value,
           -- treat NULL, empty strings and obvious placeholders as "missing"
           CASE WHEN value IS NULL OR TRIM(value) = ''
                  OR LOWER(TRIM(value)) IN ('n/a', 'na', 'unknown', 'customer', 'tbd', 'null', 'none')
                THEN 1 ELSE 0 END AS is_missing,
           -- leading/trailing spaces are a cheap, reliable "needs trimming" signal
           CASE WHEN value <> TRIM(value) THEN 1 ELSE 0 END AS has_stray_spaces
    FROM field_values
)
SELECT field,
       COUNT(*)                                                    AS total_values,
       SUM(is_missing)                                             AS missing_or_placeholder,
       ROUND(100.0 * SUM(is_missing) / COUNT(*), 1)                AS missing_pct,
       SUM(has_stray_spaces)                                       AS with_stray_spaces,
       COUNT(DISTINCT CASE WHEN is_missing = 0 THEN LOWER(TRIM(value)) END) AS distinct_values
FROM flagged
GROUP BY field
ORDER BY field;

DROP TABLE IF EXISTS source_profile;
CREATE TABLE source_profile AS
SELECT source_system,
       COUNT(*)                                                 AS records,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)       AS share_of_records_pct,   -- window over the grouped result
       SUM(CASE WHEN phone IS NULL OR TRIM(phone) = '' THEN 1 ELSE 0 END) AS blank_phone,
       SUM(CASE WHEN email IS NULL OR TRIM(email) = '' THEN 1 ELSE 0 END) AS blank_email
FROM raw_customers
GROUP BY source_system
ORDER BY source_system;
