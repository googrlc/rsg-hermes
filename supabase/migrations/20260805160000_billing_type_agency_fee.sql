-- Agency Bill + agency fees on the canonical book and commission ledger.
-- NowCerts Policy.billingType / agencyFee → finance Money workstation.

alter table public.canonical_policies
  add column if not exists billing_type text,
  add column if not exists agency_fee_amount numeric;

comment on column public.canonical_policies.billing_type is
  'NowCerts Policy.billingType: Direct Bill | Agency Bill | Direct Bill 100 | Agency Bill 100.';
comment on column public.canonical_policies.agency_fee_amount is
  'NowCerts Policy.agencyFee — fee the agency charges the insured (not carrier commission).';

alter table public.commission_ledger
  add column if not exists billing_type text,
  add column if not exists agency_fee_amount numeric;

comment on column public.commission_ledger.billing_type is
  'AMS billing type mirrored from canonical_policies. Agency Bill ≠ waiting on a carrier statement the same way.';
comment on column public.commission_ledger.agency_fee_amount is
  'Agency fee charged to the insured for this policy term. Editable; AMS value is the seed.';

-- Keep classic admin_fee_amount in sync when present (rate engine "% of Admin Fee").
do $$
begin
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'commission_ledger'
      and column_name = 'admin_fee_amount'
  ) then
    update public.commission_ledger
    set admin_fee_amount = agency_fee_amount
    where agency_fee_amount is not null
      and (admin_fee_amount is null or admin_fee_amount = 0);
  end if;
end $$;
