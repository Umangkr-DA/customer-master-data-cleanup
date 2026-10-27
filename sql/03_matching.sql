-- =============================================================================
-- 03_matching.sql
-- Purpose : Turn candidate pairs into ACCEPTED match edges (deterministic rules, no ML).
-- Input   : pair_candidates, clean_customers, SQL function name_match(a, b)
--           name_match -> 1 same person | 0 conflict | -1 a name is missing
-- Output  : match_edges (accepted pairs with rule + confidence),
--           rejected_pairs (shared key but blocked by the name guard),
--           match_edges_sym (both directions, used for clustering)
-- Decision rule
--   ACCEPT  when names agree (name_status = 1)
--   ACCEPT  when a name is missing (-1) ONLY IF phone AND email both agree
--   REJECT  otherwise  -> e.g. two relatives sharing one phone number stay separate
-- Confidence
--   High    = 2+ independent keys agree (phone / email / name+pincode)
--   Medium  = 1 key agrees AND the names agree
-- =============================================================================

DROP TABLE IF EXISTS scored_pairs;
CREATE TABLE scored_pairs AS
SELECT p.a_id, p.b_id,
       p.phone_match, p.email_match, p.name_pin_match,
       p.phone_match + p.email_match + p.name_pin_match AS keys_agreeing,
       name_match(a.full_name, b.full_name)             AS name_status
FROM pair_candidates p
JOIN clean_customers a ON a.record_id = p.a_id
JOIN clean_customers b ON b.record_id = p.b_id;

DROP TABLE IF EXISTS match_edges;
CREATE TABLE match_edges AS
SELECT a_id, b_id,
       phone_match, email_match, name_pin_match, name_status, keys_agreeing,
       -- readable rule label, e.g. 'phone+email' or 'name_pincode'
       SUBSTR(CASE WHEN phone_match    = 1 THEN '+phone'        ELSE '' END
           || CASE WHEN email_match    = 1 THEN '+email'        ELSE '' END
           || CASE WHEN name_pin_match = 1 THEN '+name_pincode' ELSE '' END, 2) AS match_rule,
       CASE WHEN keys_agreeing >= 2 THEN 'High' ELSE 'Medium' END               AS match_confidence
FROM scored_pairs
WHERE name_status = 1
   OR (name_status = -1 AND phone_match = 1 AND email_match = 1);

DROP TABLE IF EXISTS rejected_pairs;
CREATE TABLE rejected_pairs AS
SELECT s.*
FROM scored_pairs s
LEFT JOIN match_edges e ON e.a_id = s.a_id AND e.b_id = s.b_id
WHERE e.a_id IS NULL;

DROP TABLE IF EXISTS match_edges_sym;
CREATE TABLE match_edges_sym AS
SELECT a_id AS src, b_id AS dst FROM match_edges
UNION ALL
SELECT b_id, a_id FROM match_edges;
