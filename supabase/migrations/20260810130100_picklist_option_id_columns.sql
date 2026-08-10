-- Store NowCerts option IDs alongside display labels on CRM rows.
alter table public.opportunities
  add column if not exists stage_option_id uuid;

alter table public.crm_leads
  add column if not exists status_option_id uuid;

comment on column public.opportunities.stage_option_id is
  'NowCerts pipeline stage option id (see nowcerts_picklist_options)';
comment on column public.crm_leads.status_option_id is
  'NowCerts lead status option id (see nowcerts_picklist_options)';
