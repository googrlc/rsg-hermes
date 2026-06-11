-- Hermes Command Center — intake lane persistence (new CC, approved 2026-06-10).
-- cc_-prefixed to stay clear of the existing intake_submissions / hermes_files.
-- Server-side service-role access only (matches the existing Hermes table pattern).

create table if not exists cc_submissions (
  id uuid primary key default gen_random_uuid(),
  lane text not null,                          -- matches a lanes/*.yaml key
  client_name text not null,
  status text not null default 'draft',         -- draft|extracting|in_review|approved|delivered
  submission_object jsonb,                       -- serialized SubmissionObject (the spine)
  flags jsonb default '[]'::jsonb,               -- [{field, message, severity}]
  created_by text not null default 'gretchen',   -- 'lamar' | 'gretchen'
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists cc_files (
  id uuid primary key default gen_random_uuid(),
  submission_id uuid references cc_submissions(id) on delete cascade,
  filename text not null,
  doc_type text,                                 -- dec_page|mvr|drivers_license|prior_policy|...
  storage_path text not null,                    -- path in the cc-intake-uploads bucket
  size_bytes bigint,
  uploaded_at timestamptz default now()
);

create table if not exists cc_deliverables (
  id uuid primary key default gen_random_uuid(),
  submission_id uuid references cc_submissions(id) on delete cascade,
  kind text not null,                            -- from the lane's deliverables list
  title text not null,
  content text,                                  -- inline markdown (Phase 1)
  content_type text default 'text/markdown',
  storage_path text,                             -- bucket path once externalized
  status text not null default 'ready',          -- pending|ready|approved
  created_at timestamptz default now()
);

create table if not exists cc_review_events (
  id uuid primary key default gen_random_uuid(),
  submission_id uuid references cc_submissions(id) on delete cascade,
  actor text not null,
  action text not null,                          -- uploaded|extracted|flagged|fixed|approved|downloaded|crm_pushed
  detail jsonb default '{}'::jsonb,
  at timestamptz default now()
);

create index if not exists idx_cc_sub_lane_status on cc_submissions(lane, status);
create index if not exists idx_cc_events_sub on cc_review_events(submission_id, at);
create index if not exists idx_cc_files_sub on cc_files(submission_id);
create index if not exists idx_cc_deliv_sub on cc_deliverables(submission_id);

-- Private storage buckets (no public access; downloads via signed URLs only).
insert into storage.buckets (id, name, public)
values ('cc-intake-uploads', 'cc-intake-uploads', false),
       ('cc-deliverables', 'cc-deliverables', false)
on conflict (id) do nothing;
