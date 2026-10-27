-- =============================================================================
-- 04_survivorship.sql
-- Purpose : Build ONE golden record per cluster of matched source records.
-- Input   : clean_customers, match_clusters(record_id, label), match_edges, city_ref(city, pin_prefix)
-- Output  : cluster_map, golden_records
--
-- Survivorship rules (applied per field; a non-NULL value ALWAYS beats NULL, and every non-NULL
-- value is already standardized/valid, because unfixable values were set to NULL upstream):
--   full_name   -> a full first name beats an initial ("Rahul Sharma" beats "R Sharma"); then the spelling that
--                  most records in the cluster agree on (a one-off typo loses to a repeated spelling); then the
--                  longest; tie: CRM > Sales > Web
--   phone       -> Sales > CRM > Web          (Sales confirms numbers at order time)
--   email       -> Web > CRM > Sales          (customers type their own email on the Web)
--   city+pincode-> taken as a PAIR from the most complete record (both present first), tie CRM > Sales > Web;
--                  a missing half is filled from a sibling record only if it agrees with the chosen half
--   signup_date -> earliest valid date        (first time the customer appeared)
-- The *_source columns record which system each surviving value came from (audit trail).
-- =============================================================================

DROP TABLE IF EXISTS cluster_map;
CREATE TABLE cluster_map AS
SELECT record_id,
       'G' || printf('%05d', DENSE_RANK() OVER (ORDER BY label)) AS golden_id
FROM match_clusters;

-- each g_* table keeps rank 1 of ROW_NUMBER() OVER (PARTITION BY golden_id ORDER BY <rule>)
DROP TABLE IF EXISTS g_name;
CREATE TABLE g_name AS
WITH counted AS (
    SELECT m.golden_id, c.record_id, c.full_name, c.source_system,
           COUNT(*) OVER (PARTITION BY m.golden_id, c.full_name) AS name_freq
    FROM clean_customers c JOIN cluster_map m USING (record_id)
    WHERE c.full_name IS NOT NULL),
ranked AS (
    SELECT golden_id, full_name, source_system AS name_source,
           ROW_NUMBER() OVER (
               PARTITION BY golden_id
               ORDER BY CASE WHEN INSTR(full_name, ' ') > 2 THEN 0 ELSE 1 END,
                        name_freq DESC,
                        LENGTH(full_name) DESC,
                        CASE source_system WHEN 'CRM' THEN 1 WHEN 'Sales' THEN 2 ELSE 3 END,
                        record_id) AS rn
    FROM counted)
SELECT golden_id, full_name, name_source FROM ranked WHERE rn = 1;

DROP TABLE IF EXISTS g_phone;
CREATE TABLE g_phone AS
WITH ranked AS (
    SELECT m.golden_id, c.phone, c.source_system AS phone_source,
           ROW_NUMBER() OVER (
               PARTITION BY m.golden_id
               ORDER BY CASE c.source_system WHEN 'Sales' THEN 1 WHEN 'CRM' THEN 2 ELSE 3 END, c.record_id) AS rn
    FROM clean_customers c JOIN cluster_map m USING (record_id)
    WHERE c.phone IS NOT NULL)
SELECT golden_id, phone, phone_source FROM ranked WHERE rn = 1;

DROP TABLE IF EXISTS g_email;
CREATE TABLE g_email AS
WITH ranked AS (
    SELECT m.golden_id, c.email, c.source_system AS email_source,
           ROW_NUMBER() OVER (
               PARTITION BY m.golden_id
               ORDER BY CASE c.source_system WHEN 'Web' THEN 1 WHEN 'CRM' THEN 2 ELSE 3 END, c.record_id) AS rn
    FROM clean_customers c JOIN cluster_map m USING (record_id)
    WHERE c.email IS NOT NULL)
SELECT golden_id, email, email_source FROM ranked WHERE rn = 1;

DROP TABLE IF EXISTS g_date;
CREATE TABLE g_date AS
WITH ranked AS (
    SELECT m.golden_id, c.signup_date, c.source_system AS date_source,
           ROW_NUMBER() OVER (PARTITION BY m.golden_id ORDER BY c.signup_date ASC, c.record_id) AS rn
    FROM clean_customers c JOIN cluster_map m USING (record_id)
    WHERE c.signup_date IS NOT NULL)
SELECT golden_id, signup_date, date_source FROM ranked WHERE rn = 1;

-- address: rank by completeness of the city+pincode pair, then by source priority
DROP TABLE IF EXISTS g_addr_best;
CREATE TABLE g_addr_best AS
WITH ranked AS (
    SELECT m.golden_id, c.city, c.pincode, c.source_system AS address_source,
           ROW_NUMBER() OVER (
               PARTITION BY m.golden_id
               ORDER BY CASE WHEN c.city IS NOT NULL AND c.pincode IS NOT NULL THEN 0
                             WHEN c.city IS NOT NULL OR  c.pincode IS NOT NULL THEN 1
                             ELSE 2 END,
                        CASE c.source_system WHEN 'CRM' THEN 1 WHEN 'Sales' THEN 2 ELSE 3 END,
                        c.record_id) AS rn
    FROM clean_customers c JOIN cluster_map m USING (record_id))
SELECT golden_id, city, pincode, address_source FROM ranked WHERE rn = 1;

-- fill a missing pincode from a sibling record whose pincode agrees with the chosen city
DROP TABLE IF EXISTS g_pin_fill;
CREATE TABLE g_pin_fill AS
WITH ranked AS (
    SELECT b.golden_id, c.pincode,
           ROW_NUMBER() OVER (PARTITION BY b.golden_id ORDER BY c.record_id) AS rn
    FROM g_addr_best b
    JOIN city_ref r         ON r.city = b.city
    JOIN cluster_map m      ON m.golden_id = b.golden_id
    JOIN clean_customers c  ON c.record_id = m.record_id
    WHERE b.pincode IS NULL AND c.pincode IS NOT NULL
      AND SUBSTR(c.pincode, 1, 3) = r.pin_prefix)
SELECT golden_id, pincode FROM ranked WHERE rn = 1;

-- fill a missing city from a sibling record whose city agrees with the chosen pincode prefix
DROP TABLE IF EXISTS g_city_fill;
CREATE TABLE g_city_fill AS
WITH ranked AS (
    SELECT b.golden_id, c.city,
           ROW_NUMBER() OVER (PARTITION BY b.golden_id ORDER BY c.record_id) AS rn
    FROM g_addr_best b
    JOIN cluster_map m      ON m.golden_id = b.golden_id
    JOIN clean_customers c  ON c.record_id = m.record_id
    JOIN city_ref r         ON r.city = c.city
    WHERE b.city IS NULL AND b.pincode IS NOT NULL
      AND SUBSTR(b.pincode, 1, 3) = r.pin_prefix)
SELECT golden_id, city FROM ranked WHERE rn = 1;

-- how each cluster was formed (rules used inside the cluster); inputs are pre-sorted so the
-- GROUP_CONCAT order is deterministic on every SQLite version
DROP TABLE IF EXISTS g_rules;
CREATE TABLE g_rules AS
WITH edge_rules AS (
    SELECT DISTINCT m.golden_id, e.match_rule
    FROM match_edges e JOIN cluster_map m ON m.record_id = e.a_id
    ORDER BY m.golden_id, e.match_rule),
rule_list AS (
    SELECT golden_id, GROUP_CONCAT(match_rule, ', ') AS match_rules FROM edge_rules GROUP BY golden_id),
confidence AS (
    SELECT m.golden_id,
           CASE WHEN SUM(CASE WHEN e.match_confidence = 'Medium' THEN 1 ELSE 0 END) > 0
                THEN 'Medium' ELSE 'High' END AS lowest_match_confidence
    FROM match_edges e JOIN cluster_map m ON m.record_id = e.a_id
    GROUP BY m.golden_id)
SELECT l.golden_id, l.match_rules, c.lowest_match_confidence
FROM rule_list l JOIN confidence c USING (golden_id);

DROP TABLE IF EXISTS golden_records;
CREATE TABLE golden_records AS
WITH ordered_members AS (            -- sorted once so GROUP_CONCAT output is deterministic
    SELECT m.golden_id, m.record_id, c.source_system
    FROM cluster_map m JOIN clean_customers c USING (record_id)
    ORDER BY m.golden_id, m.record_id),
ordered_systems AS (
    SELECT DISTINCT golden_id, source_system FROM ordered_members ORDER BY golden_id, source_system),
system_list AS (
    SELECT golden_id, GROUP_CONCAT(source_system, ',') AS source_systems
    FROM ordered_systems GROUP BY golden_id),
cluster_stats AS (
    SELECT o.golden_id,
           COUNT(*)                       AS n_source_records,
           s.source_systems,
           GROUP_CONCAT(o.record_id, ' | ') AS merged_record_ids
    FROM ordered_members o JOIN system_list s USING (golden_id)
    GROUP BY o.golden_id)
SELECT s.golden_id,
       n.full_name,
       p.phone,
       e.email,
       COALESCE(a.city, cf.city)       AS city,
       COALESCE(a.pincode, pf.pincode) AS pincode,
       d.signup_date,
       n.name_source, p.phone_source, e.email_source, a.address_source, d.date_source,
       s.n_source_records,
       s.source_systems,
       s.merged_record_ids,
       r.match_rules,
       r.lowest_match_confidence
FROM cluster_stats s
LEFT JOIN g_name n       USING (golden_id)
LEFT JOIN g_phone p      USING (golden_id)
LEFT JOIN g_email e      USING (golden_id)
LEFT JOIN g_date d       USING (golden_id)
LEFT JOIN g_addr_best a  USING (golden_id)
LEFT JOIN g_pin_fill pf  USING (golden_id)
LEFT JOIN g_city_fill cf USING (golden_id)
LEFT JOIN g_rules r      USING (golden_id)
ORDER BY s.golden_id;
