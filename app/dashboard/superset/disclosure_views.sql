-- Public-safe view: no sku-level grouping exposed.
CREATE OR REPLACE VIEW disclosure_public_daily AS
SELECT
  dr.disclosure_id,
  dr.policy_id,
  dr.period_start::date AS period_day,
  dm.metric_key,
  dm.value
FROM disclosure_runs dr
JOIN disclosure_metrics dm ON dm.disclosure_id = dr.disclosure_id
WHERE dr.policy_id = 'policy_public_v1';

-- Investor view: includes grouped breakdown but still redacted by policy.
CREATE OR REPLACE VIEW disclosure_investor_grouped AS
SELECT
  dr.disclosure_id,
  dr.policy_id,
  dr.period_start::date AS period_day,
  dgm.metric_key,
  dgm.group_json,
  COALESCE(dgm.group_json->>'channel', '') AS channel,
  COALESCE(dgm.group_json->>'region', '') AS region,
  COALESCE(dgm.group_json->>'sku', '') AS sku,
  dgm.value
FROM disclosure_runs dr
JOIN disclosure_grouped_metrics dgm ON dgm.disclosure_id = dr.disclosure_id
WHERE dr.policy_id = 'policy_investor_v1';
