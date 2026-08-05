-- NowCerts cancellationDate → canonical_policies / commission_ledger.
-- Mid-term cancels keep the original expiration_date; cancellation_date is the
-- real cutoff used by the finance portal to estimate chargebacks.

alter table public.canonical_policies
  add column if not exists cancellation_date date;

comment on column public.canonical_policies.cancellation_date is
  'NowCerts Policy.cancellationDate. Distinct from expiration_date (original term end).';

alter table public.commission_ledger
  add column if not exists cancellation_date date;

comment on column public.commission_ledger.cancellation_date is
  'AMS cancellation date mirrored from canonical_policies. Distinct from policy_expiration_date.';
