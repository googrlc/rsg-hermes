-- Hermes fixtures matching blueprint viewer pages ~2–6.
-- Synthetic Slack IDs (C09RSG…): replace with real channel IDs before production Slack wiring.

-- ── Page 2: Slack channel registry ─────────────────────────────────────────────
INSERT INTO public.slack_registry (channel_id, channel_name, designated_purpose, allowed_ai_roles, is_active)
VALUES
  (
    'C09RSGHMSAUD',
    '#rsg-hermes-commission-audit',
    'Commission variance alerts, discrepancy threads, auditor handoffs.',
    jsonb_build_array('HermesCommissionAuditor', 'HermesFinanceOps'),
    true
  ),
  (
    'C09RSGHMSREN',
    '#rsg-hermes-project85-renewals',
    'Project 85 renewal work queue, SLA nudges, carrier escalations.',
    jsonb_build_array('HermesRenewalSpecialist', 'HermesOpsRouter'),
    true
  ),
  (
    'C09RSGHMSOPS',
    '#rsg-hermes-operations',
    'Daily digest, cron health, non-sensitive ops KPI pings.',
    jsonb_build_array('HermesOpsRouter', 'HermesFinanceOps'),
    true
  )
ON CONFLICT (channel_id) DO UPDATE SET
  channel_name       = excluded.channel_name,
  designated_purpose = excluded.designated_purpose,
  allowed_ai_roles   = excluded.allowed_ai_roles,
  is_active          = excluded.is_active;

-- ── Page 3: Hermes AI roles ────────────────────────────────────────────────────
INSERT INTO public.hermes_ai_roles (role_name, system_prompt_id, success_criteria, permissions)
VALUES
  (
    'HermesCommissionAuditor',
    'rsghms_prompt_commission_aud_v1',
    jsonb_build_object(
      'signals', jsonb_build_array('variance_explained_or_zero', 'eom_rollups_balanced'),
      'no_hallucinated_policy_numbers', true
    ),
    jsonb_build_object(
      'scope', jsonb_build_array('commission_audits', 'eom_scorecards', 'crm_write_queue'),
      'crm_writes', 'queue_only'
    )
  ),
  (
    'HermesRenewalSpecialist',
    'rsghms_prompt_project85_v1',
    jsonb_build_object(
      'signals', jsonb_build_array('action_log_complete', 'risk_status_from_enum'),
      'escalations_to_slack', true
    ),
    jsonb_build_object(
      'scope', jsonb_build_array('project_85_renewals', 'renewal_actions', 'slack_registry'),
      'crm_writes', 'queue_only'
    )
  ),
  (
    'HermesFinanceOps',
    'rsghms_prompt_finops_v1',
    jsonb_build_object(
      'signals', jsonb_build_array('scorecard_numbers_traceable_to_audits'),
      'month_lock_respected', true
    ),
    jsonb_build_object(
      'scope', jsonb_build_array('commission_audits', 'eom_scorecards', 'dashboard_kpis'),
      'crm_writes', 'none'
    )
  ),
  (
    'HermesOpsRouter',
    'rsghms_prompt_router_v1',
    jsonb_build_object(
      'signals', jsonb_build_array('posts_only_via_registry_channels', 'guardrail_on_unknown_slack'),
      'require_registry_hit', true
    ),
    jsonb_build_object(
      'scope', jsonb_build_array('slack_registry', 'reporting_schedules', 'guardrail_logs'),
      'crm_writes', 'queue_only'
    )
  )
ON CONFLICT (role_name) DO UPDATE SET
  system_prompt_id = excluded.system_prompt_id,
  success_criteria = excluded.success_criteria,
  permissions      = excluded.permissions;

-- ── Pages 5–6: CRM queue + receipts ────────────────────────────────────────────
INSERT INTO public.crm_write_queue (
  id,
  target_system,
  entity_type,
  entity_id,
  payload,
  status,
  attempt_count,
  created_by_role
)
VALUES
  (
    'a1000001-aaaa-aaaa-aaaa-aaaaaaaaaaaa'::uuid,
    'HubSpot',
    'Contact',
    'crm-seed-contact-4401',
    jsonb_build_object(
      'intent', 'annotate',
      'properties', jsonb_build_object('lifecycle_stage_note', 'Hermes seed: VIP trucking GL schedule QBR')
    ),
    'SUCCESS'::public.sync_status,
    1,
    'HermesRenewalSpecialist'
  ),
  (
    'a1000002-aaaa-aaaa-aaaa-aaaaaaaaaaaa'::uuid,
    'HubSpot',
    'Deal',
    'crm-seed-deal-9812',
    jsonb_build_object('intent', 'create_note', 'body', 'Hermes proof-of-queue staged before worker execution.'),
    'PENDING'::public.sync_status,
    0,
    'HermesCommissionAuditor'
  ),
  (
    'a1000003-aaaa-aaaa-aaaa-aaaaaaaaaaaa'::uuid,
    'HubSpot',
    'Note',
    'crm-seed-deal-9812',
    jsonb_build_object(
      'intent', 'log_discrepancy',
      'deal_id', '9812',
      'text', 'Hermes blocked direct CRM write drill — use queue'
    ),
    'FAILED'::public.sync_status,
    3,
    'HermesFinanceOps'
  )
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.crm_receipts (id, queue_id, transaction_id, raw_response)
VALUES (
  'b2000001-bbbb-bbbb-bbbb-bbbbbbbbbbbb'::uuid,
  'a1000001-aaaa-aaaa-aaaa-aaaaaaaaaaaa'::uuid,
  'hs_seed_txn_99331',
  jsonb_build_object(
    'status', '200',
    'provider', 'hubspot',
    'result', jsonb_build_object('updated', true, 'id', 'crm-seed-contact-4401'),
    'correlation', 'hermes_seed_v1'
  )
)
ON CONFLICT (id) DO NOTHING;

-- ── Page 4: Commission audits + EOM scorecard ──────────────────────────────────
INSERT INTO public.commission_audits (
  statement_id,
  carrier,
  policy_number,
  client_name,
  expected_amount,
  received_amount,
  status,
  discrepancy_notes,
  snapshot_month
)
VALUES
  (
    'STMT-HMS-2026-05-U01',
    'National General',
    'GL-RSG-900441',
    'Acme Freight LLC',
    12480.50,
    12480.50,
    'MATCHED'::public.commission_status,
    NULL,
    '2026-05-01'::date
  ),
  (
    'STMT-HMS-2026-05-U01',
    'Amerisure',
    'WC-RSG-220981',
    'Summit Scaffold Co.',
    4320.00,
    3890.00,
    'DISCREPANCY'::public.commission_status,
    '$430 variance carrier clawback memo 5/12 referenced',
    '2026-05-01'::date
  ),
  (
    'STMT-HMS-2026-05-U02',
    'Nationwide Specialty',
    'PL-RSG-77102',
    'Harper Household',
    910.25,
    NULL,
    'PENDING'::public.commission_status,
    'Awaiting May ACH detail file',
    '2026-05-01'::date
  )
ON CONFLICT (statement_id, policy_number, snapshot_month) DO NOTHING;

INSERT INTO public.eom_scorecards (
  snapshot_month,
  total_expected,
  total_received,
  total_variance,
  discrepancy_count,
  reconciled_count,
  kpi_json,
  is_locked
)
VALUES (
  '2026-05-01'::date,
  17710.75,
  17370.50,
  -340.25,
  1,
  1,
  jsonb_build_object(
    'open_discrepancies', 1,
    'carriers_reviewed', jsonb_build_array('National General', 'Amerisure', 'Nationwide Specialty'),
    'coverage_pct_agency', 94.6
  ),
  false
)
ON CONFLICT (snapshot_month) DO NOTHING;

-- ── Page 5: renewals ──────────────────────────────────────────────────────────
INSERT INTO public.project_85_renewals (
  id,
  policy_number,
  client_name,
  expiration_date,
  premium_current,
  premium_renewal,
  risk_status,
  ai_strategy_notes,
  last_contact_date
)
VALUES
  (
    'c3000003-cccc-cccc-cccc-cccccccccccc'::uuid,
    'AUTO-RSG-118822',
    'Martinez Courier Services',
    '2026-07-15'::date,
    8840.00,
    9620.00,
    'AT_RISK'::public.renewal_risk_status,
    'Moderate increase; confirm mileage class with insured before quote.',
    '2026-04-21'::date
  ),
  (
    'c3000004-cccc-cccc-cccc-cccccccccccc'::uuid,
    'BOP-RSG-77301',
    'Blue Ridge Dental Co-op',
    '2026-08-03'::date,
    6210.00,
    6188.50,
    'SAFE'::public.renewal_risk_status,
    'Flat-ish renewal proactive thank-you cross-sell GL tail.',
    '2026-04-28'::date
  )
ON CONFLICT (policy_number) DO UPDATE SET
  client_name       = excluded.client_name,
  expiration_date   = excluded.expiration_date,
  premium_current   = excluded.premium_current,
  premium_renewal   = excluded.premium_renewal,
  risk_status       = excluded.risk_status,
  ai_strategy_notes = excluded.ai_strategy_notes,
  last_contact_date = excluded.last_contact_date;

INSERT INTO public.renewal_actions (
  id,
  renewal_id,
  action_type,
  details,
  performed_by_role
)
VALUES
  (
    'd4000001-dddd-dddd-dddd-dddddddddddd'::uuid,
    'c3000003-cccc-cccc-cccc-cccccccccccc'::uuid,
    'SLACK_ALERT',
    jsonb_build_object('channel_id', 'C09RSGHMSREN', 'severity', 'normal', 'template', 'renewal_at_risk_nudge'),
    'HermesRenewalSpecialist'
  ),
  (
    'd4000002-dddd-dddd-dddd-dddddddddddd'::uuid,
    'c3000003-cccc-cccc-cccc-cccccccccccc'::uuid,
    'EMAIL_SENT',
    jsonb_build_object(
      'mailbox', 'agency_outbound',
      'subject_seed', 'Renewal options Martinez Courier seed'
    ),
    'HermesRenewalSpecialist'
  ),
  (
    'd4000003-dddd-dddd-dddd-dddddddddddd'::uuid,
    'c3000004-cccc-cccc-cccc-cccccccccccc'::uuid,
    'QUOTE_GENERATED',
    jsonb_build_object('carrier_candidates', jsonb_build_array('Nationwide', 'Travelers'), 'lob', 'BOP'),
    'HermesRenewalSpecialist'
  )
ON CONFLICT (id) DO NOTHING;

-- ── Page 6: guardrail event ────────────────────────────────────────────────────
INSERT INTO public.guardrail_logs (
  id,
  agent_role,
  attempted_action,
  rule_violated,
  context_payload,
  severity
)
VALUES (
  'e5000001-eeee-eeee-eeee-eeeeeeeeeeee'::uuid,
  'HermesOpsRouter',
  'slack.post_message',
  'slack_registry_miss',
  jsonb_build_object(
    'requested_channel_name', 'random_human_dm_firehose',
    'resolver', 'Hermes Slack guard seed'
  ),
  'HIGH'
)
ON CONFLICT (id) DO NOTHING;

-- ── Page 6: reporting schedules ───────────────────────────────────────────────
INSERT INTO public.reporting_schedules (
  id,
  report_name,
  frequency,
  target_slack_channel,
  kpi_query_config,
  last_run,
  next_run
)
VALUES
  (
    'f6000001-f001-f001-f001-f001f001f001'::uuid,
    'Hermes Daily Ops Pulse',
    'DAILY'::public.report_frequency,
    'C09RSGHMSOPS',
    jsonb_build_object('source', 'dashboard_kpis', 'category', 'SYSTEM_HEALTH', 'window_hours', 36),
    now() AT TIME ZONE 'utc' - interval '26 hours',
    now() AT TIME ZONE 'utc' + interval '2 hours'
  ),
  (
    'f6000002-f002-f002-f002-f002f002f002'::uuid,
    'Commission Discrepancy Rollup',
    'WEEKLY'::public.report_frequency,
    'C09RSGHMSAUD',
    jsonb_build_object(
      'tables', jsonb_build_array('commission_audits'),
      'filters', jsonb_build_object('status', jsonb_build_array('DISCREPANCY', 'ESCALATED'))
    ),
    now() AT TIME ZONE 'utc' - interval '5 days',
    now() AT TIME ZONE 'utc' + interval '2 days'
  ),
  (
    'f6000003-f003-f003-f003-f003f003f003'::uuid,
    'Renewal SLA Premium Delta',
    'MONTHLY'::public.report_frequency,
    'C09RSGHMSREN',
    jsonb_build_object(
      'tables', jsonb_build_array('project_85_renewals'),
      'focus', jsonb_build_array('risk_status', 'expiration_date'),
      'cutoff_iso_week', '2026-W18'
    ),
    NULL,
    '2026-06-02 14:30:00+00'::timestamptz
  )
ON CONFLICT (id) DO NOTHING;

-- ── KPI snapshots — deterministic IDs (idempotent) ─────────────────────────────
INSERT INTO public.dashboard_kpis (id, metric_name, metric_value, category, recorded_at)
VALUES
  (
    'a7021111-1111-1111-1111-111111111101'::uuid,
    'open_commission_audit_exceptions',
    2,
    'FINANCE',
    now() AT TIME ZONE 'utc' - interval '1 hour'
  ),
  (
    'a7021111-1111-1111-1111-111111111102'::uuid,
    'project85_renewals_at_risk_pct',
    14.8,
    'RENEWALS',
    now() AT TIME ZONE 'utc' - interval '1 hour'
  ),
  (
    'a7021111-1111-1111-1111-111111111103'::uuid,
    'crm_queue_backlog_age_max_minutes',
    112,
    'SYSTEM_HEALTH',
    now() AT TIME ZONE 'utc' - interval '2 hours'
  ),
  (
    'a7021111-1111-1111-1111-111111111104'::uuid,
    'guardrail_events_24h',
    7,
    'SYSTEM_HEALTH',
    now() AT TIME ZONE 'utc' - interval '3 hours'
  ),
  (
    'a7021111-1111-1111-1111-111111111105'::uuid,
    'slack_registry_channels_active_seed',
    3,
    'SYSTEM_HEALTH',
    now() AT TIME ZONE 'utc' - interval '4 hours'
  )
ON CONFLICT (id) DO NOTHING;
