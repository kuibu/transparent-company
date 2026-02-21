DROP VIEW IF EXISTS disclosure_public_daily CASCADE;
CREATE VIEW disclosure_public_daily AS
SELECT
  dr.disclosure_id,
  dr.policy_id,
  dr.period_start::date AS period_day,
  dm.metric_key,
  dm.value
FROM disclosure_runs dr
JOIN disclosure_metrics dm ON dm.disclosure_id = dr.disclosure_id
WHERE dr.policy_id = 'policy_public_v1';

DROP VIEW IF EXISTS disclosure_investor_grouped CASCADE;
CREATE VIEW disclosure_investor_grouped AS
SELECT
  dr.disclosure_id,
  dr.policy_id,
  dr.period_start::date AS period_day,
  dgm.metric_key,
  dgm.group_json,
  COALESCE(dgm.group_json->>'channel', '') AS channel,
  COALESCE(dgm.group_json->>'region', '') AS region,
  COALESCE(dgm.group_json->>'store_id', '') AS store_id,
  COALESCE(dgm.group_json->>'time_slot', '') AS time_slot,
  COALESCE(dgm.group_json->>'promotion_id', '') AS promotion_id,
  COALESCE(dgm.group_json->>'promotion_phase', '') AS promotion_phase,
  COALESCE(dgm.group_json->>'sku', '') AS sku,
  COALESCE(dgm.group_json->>'category', '') AS category,
  COALESCE(dgm.group_json->>'payment_term_bucket', '') AS payment_term_bucket,
  dgm.value
FROM disclosure_runs dr
JOIN disclosure_grouped_metrics dgm ON dgm.disclosure_id = dr.disclosure_id
WHERE dr.policy_id = 'policy_investor_v1';

DROP VIEW IF EXISTS disclosure_public_kpi_base CASCADE;
CREATE VIEW disclosure_public_kpi_base AS
SELECT
  dr.disclosure_id,
  dr.policy_id,
  dr.period_start,
  dr.period_end,
  dr.period_start::date AS period_start_date,
  (dr.period_end::date - dr.period_start::date) AS period_days,
  CASE
    WHEN (dr.period_end::date - dr.period_start::date) = 1 THEN 'day'
    WHEN (dr.period_end::date - dr.period_start::date) = 7 THEN 'week'
    ELSE 'month'
  END AS period_granularity,
  COALESCE(SUM(CASE WHEN dm.metric_key = 'revenue_cents' THEN dm.value END), 0) / 100.0 AS revenue_yuan,
  COALESCE(SUM(CASE WHEN dm.metric_key = 'refund_rate_bps' THEN dm.value END), 0) / 100.0 AS refund_rate_pct,
  COALESCE(SUM(CASE WHEN dm.metric_key = 'inventory_loss_rate_bps' THEN dm.value END), 0) / 100.0 AS inventory_loss_rate_pct,
  COALESCE(SUM(CASE WHEN dm.metric_key = 'conflict_rate_bps' THEN dm.value END), 0) / 100.0 AS conflict_rate_pct,
  COALESCE(SUM(CASE WHEN dm.metric_key = 'avg_order_value_cents' THEN dm.value END), 0) / 100.0 AS avg_order_value_yuan,
  COALESCE(SUM(CASE WHEN dm.metric_key = 'repeat_purchase_rate_bps' THEN dm.value END), 0) / 100.0 AS repeat_purchase_rate_pct,
  COALESCE(SUM(CASE WHEN dm.metric_key = 'slow_moving_sku_ratio_bps' THEN dm.value END), 0) / 100.0 AS slow_moving_sku_ratio_pct,
  COALESCE(SUM(CASE WHEN dm.metric_key = 'qc_fail_rate_bps' THEN dm.value END), 0) / 100.0 AS qc_fail_rate_pct,
  COALESCE(SUM(CASE WHEN dm.metric_key = 'compensation_ratio_bps' THEN dm.value END), 0) / 100.0 AS compensation_ratio_pct,
  COALESCE(SUM(CASE WHEN dm.metric_key = 'complaint_resolution_hours_avg' THEN dm.value END), 0) AS complaint_resolution_hours_avg,
  COALESCE(SUM(CASE WHEN dm.metric_key = 'inventory_turnover_days' THEN dm.value END), 0) AS inventory_turnover_days,
  COALESCE(SUM(CASE WHEN dm.metric_key = 'operating_cash_net_inflow_cents' THEN dm.value END), 0) / 100.0 AS operating_cash_net_inflow_yuan,
  COALESCE(SUM(CASE WHEN dm.metric_key = 'supplier_payment_term_days_avg' THEN dm.value END), 0) AS supplier_payment_term_days_avg,
  COALESCE(SUM(CASE WHEN dm.metric_key = 'supplier_term_short_ratio_bps' THEN dm.value END), 0) / 100.0 AS supplier_term_short_pct,
  COALESCE(SUM(CASE WHEN dm.metric_key = 'supplier_term_mid_ratio_bps' THEN dm.value END), 0) / 100.0 AS supplier_term_mid_pct,
  COALESCE(SUM(CASE WHEN dm.metric_key = 'supplier_term_long_ratio_bps' THEN dm.value END), 0) / 100.0 AS supplier_term_long_pct
FROM disclosure_runs dr
LEFT JOIN disclosure_metrics dm ON dm.disclosure_id = dr.disclosure_id
WHERE dr.policy_id = 'policy_public_v1'
GROUP BY dr.disclosure_id, dr.policy_id, dr.period_start, dr.period_end, dr.period_start::date, (dr.period_end::date - dr.period_start::date);

DROP VIEW IF EXISTS disclosure_public_daily_kpi_pretty CASCADE;
CREATE VIEW disclosure_public_daily_kpi_pretty AS
SELECT *
FROM disclosure_public_kpi_base
WHERE period_granularity = 'day';

DROP VIEW IF EXISTS disclosure_public_weekly_kpi_pretty CASCADE;
CREATE VIEW disclosure_public_weekly_kpi_pretty AS
SELECT *
FROM disclosure_public_kpi_base
WHERE period_granularity = 'week';

DROP VIEW IF EXISTS disclosure_public_monthly_kpi_pretty CASCADE;
CREATE VIEW disclosure_public_monthly_kpi_pretty AS
SELECT *
FROM disclosure_public_kpi_base
WHERE period_granularity = 'month';

DROP VIEW IF EXISTS disclosure_investor_revenue_dimension_pretty CASCADE;
CREATE VIEW disclosure_investor_revenue_dimension_pretty AS
SELECT
  dr.disclosure_id,
  dr.policy_id,
  dr.period_start,
  dr.period_end,
  dr.period_start::date AS period_start_date,
  (dr.period_end::date - dr.period_start::date) AS period_days,
  CASE
    WHEN (dr.period_end::date - dr.period_start::date) = 1 THEN 'day'
    WHEN (dr.period_end::date - dr.period_start::date) = 7 THEN 'week'
    ELSE 'month'
  END AS period_granularity,
  COALESCE(dgm.group_json->>'channel', '') AS channel,
  COALESCE(dgm.group_json->>'region', '') AS region,
  COALESCE(dgm.group_json->>'store_id', '') AS store_id,
  COALESCE(dgm.group_json->>'time_slot', '') AS time_slot,
  COALESCE(dgm.group_json->>'promotion_id', '') AS promotion_id,
  COALESCE(dgm.group_json->>'promotion_phase', '') AS promotion_phase,
  COALESCE(dgm.group_json->>'sku', '') AS sku,
  COALESCE(dgm.group_json->>'category', '') AS category,
  dgm.value / 100.0 AS revenue_yuan
FROM disclosure_runs dr
JOIN disclosure_grouped_metrics dgm ON dgm.disclosure_id = dr.disclosure_id
WHERE dr.policy_id = 'policy_investor_v1'
  AND dgm.metric_key = 'revenue_cents';

DROP VIEW IF EXISTS disclosure_investor_supplier_term_pretty CASCADE;
CREATE VIEW disclosure_investor_supplier_term_pretty AS
SELECT
  dr.disclosure_id,
  dr.policy_id,
  dr.period_start,
  dr.period_end,
  dr.period_start::date AS period_start_date,
  (dr.period_end::date - dr.period_start::date) AS period_days,
  CASE
    WHEN (dr.period_end::date - dr.period_start::date) = 1 THEN 'day'
    WHEN (dr.period_end::date - dr.period_start::date) = 7 THEN 'week'
    ELSE 'month'
  END AS period_granularity,
  COALESCE(dgm.group_json->>'payment_term_bucket', 'unknown') AS payment_term_bucket,
  dgm.value / 100.0 AS settlement_yuan,
  ROUND(
    100.0 * dgm.value / NULLIF(SUM(dgm.value) OVER (PARTITION BY dr.disclosure_id), 0),
    2
  ) AS share_pct
FROM disclosure_runs dr
JOIN disclosure_grouped_metrics dgm ON dgm.disclosure_id = dr.disclosure_id
WHERE dr.policy_id IN ('policy_public_v1', 'policy_investor_v1')
  AND dgm.metric_key = 'supplier_settlement_cents';

DROP VIEW IF EXISTS disclosure_public_kpi_pretty CASCADE;
CREATE VIEW disclosure_public_kpi_pretty AS
SELECT *
FROM disclosure_public_monthly_kpi_pretty;

DROP VIEW IF EXISTS disclosure_investor_mix_pretty CASCADE;
CREATE VIEW disclosure_investor_mix_pretty AS
SELECT
  disclosure_id,
  policy_id,
  period_start_date AS period_day,
  'revenue_cents' AS metric_key,
  jsonb_build_object(
    'channel', channel,
    'region', region,
    'store_id', store_id,
    'time_slot', time_slot,
    'promotion_phase', promotion_phase,
    'category', category,
    'sku', sku
  ) AS group_json,
  channel,
  region,
  store_id,
  time_slot,
  sku,
  category,
  revenue_yuan,
  0.0::double precision AS share_pct
FROM disclosure_investor_revenue_dimension_pretty;
