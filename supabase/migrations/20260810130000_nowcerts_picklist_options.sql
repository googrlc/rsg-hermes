-- NowCerts-aligned picklist options (IDs, not free-form labels).
-- Seeded with stable UUIDs derived from list_key+label so CRM rows can store
-- option_id and resolve a display label. Sync may refresh from the gateway
-- without rewriting IDs for known labels.

create table if not exists public.nowcerts_picklist_options (
  list_key    text not null,
  option_id   uuid not null,
  label       text not null,
  sort_order  int not null default 0,
  active      boolean not null default true,
  primary key (list_key, option_id)
);

create unique index if not exists nowcerts_picklist_options_list_label_uidx
  on public.nowcerts_picklist_options (list_key, lower(label));

comment on table public.nowcerts_picklist_options is
  'NowCerts option IDs for CRM lead statuses, pipeline stages, renewal statuses, endorsement types';

insert into public.nowcerts_picklist_options (list_key, option_id, label, sort_order, active)
values
  ('pipeline_new_business', 'd82f13b9-f96d-d154-cdf9-251cc0f03c93', 'Not Assigned', 0, true),
  ('pipeline_new_business', '9c3bae52-2c28-2c49-ffe7-226c5417c41c', 'Preparing Application', 1, true),
  ('pipeline_new_business', '09092bd2-3444-429a-7ff6-fdb58f77a68b', 'Sent For Quoting', 2, true),
  ('pipeline_new_business', '092bb8e4-18f6-fa69-4263-5ca10f78826f', 'Quotes Received', 3, true),
  ('pipeline_new_business', 'e3047f00-88c6-930d-5683-9761bae9f632', 'Sent Proposal', 4, true),
  ('pipeline_new_business', '16f23fa8-2515-e6ec-9e11-423849b4d2f1', 'Request to Bind', 5, true),
  ('pipeline_new_business', '82f61678-caea-09f8-e731-5f696449ee0d', 'Bound / Won', 6, true),
  ('pipeline_new_business', 'c9ad07f3-689b-36b4-928c-0d7b8597f154', 'Lost', 7, true),
  ('pipeline_renewal', 'bb6eb18f-8b31-cf43-3b57-45cea520183a', 'Renewal in 90 days', 8, true),
  ('pipeline_renewal', 'f7ffbbe0-2f08-3e1a-5765-f5fe6e8ce997', 'Renewal in 60 days', 9, true),
  ('pipeline_renewal', '0c76b0dc-acf4-72f1-9a01-1222dede624f', 'Renewal in 30 days', 10, true),
  ('pipeline_renewal', '9fea61ef-40d1-c7a8-58b5-01b7a74c617b', 'Requote Renewal', 11, true),
  ('pipeline_renewal', 'b917834c-4262-9863-6720-f912daa6f219', 'Annual Policy Review', 12, true),
  ('pipeline_renewal', '8eb2161c-1925-43d0-602b-5c1486f93def', 'Complete/Auto-Renewal', 13, true),
  ('pipeline_renewal', '76a8a582-6a6f-dbf7-2929-50096e26cb50', 'Bound / Won', 14, true),
  ('pipeline_renewal', 'cbad2c95-ef0a-94f2-a534-fc38f2907b02', 'Not Renewed', 15, true),
  ('lead_status', 'b2e18587-57b1-91bc-6b1a-d7690c1a4618', 'new', 16, true),
  ('lead_status', '7d82ec17-15db-7f3d-2744-a0ade49c15a0', 'working', 17, true),
  ('lead_status', '51836de6-5193-2fbc-ca70-a9fec7e41946', 'quoted', 18, true),
  ('lead_status', 'a97b0b79-7b68-43b1-dd2f-1c1f5ce5a9e8', 'converted', 19, true),
  ('lead_status', 'ab69f551-f77f-9a65-bb98-5ffd4da476ba', 'lost', 20, true),
  ('renewal_status', '95a4fc61-bc52-c296-1113-156e343b35da', 'Up for Renewal', 21, true),
  ('renewal_status', '3d01d3f4-d334-f3ed-39a5-e04e0381b56d', 'Renewing', 22, true),
  ('renewal_status', '3dd5b4be-44be-f13e-842a-e814774a6041', 'Renewed', 23, true),
  ('renewal_status', 'c35d86b7-fb74-2a17-f734-7c1395c51713', 'Non-Renewed', 24, true),
  ('renewal_status', 'a2b134fa-5416-5873-9493-979b501e6f2c', 'Cancelled', 25, true),
  ('endorsement_type', '1966a48e-07a5-f5bb-9e1d-f2f862318381', 'Add Driver', 26, true),
  ('endorsement_type', 'a9ce714b-c49d-94c6-f95b-d7def7f6de96', 'Remove Driver', 27, true),
  ('endorsement_type', '2c3c659a-7568-63a8-5ef9-a17d0fb45d54', 'Replace Driver', 28, true),
  ('endorsement_type', '3669f7eb-0878-d3ae-204a-10dde33d2b44', 'Add Vehicle', 29, true),
  ('endorsement_type', '384baf5f-0058-db81-0d7b-1963b3a7792a', 'Replace Vehicle', 30, true),
  ('endorsement_type', '5c7583c3-2288-eeb5-0b77-3dc08307f0bf', 'Address Change', 31, true),
  ('endorsement_type', '91feb5aa-2fce-7963-06e6-b66204cdaf55', 'Coverage Change', 32, true),
  ('endorsement_type', '391d6b38-2742-a68f-90bf-05c435824a96', 'Policy Change', 33, true),
  ('endorsement_type', '39b78879-21ce-6f4f-59d4-b153c330295f', 'Certificate of Insurance', 34, true),
  ('endorsement_type', 'd92e98fd-533c-1958-e938-3a8ad75065aa', 'Other', 35, true)
on conflict (list_key, option_id) do update
  set label = excluded.label,
      sort_order = excluded.sort_order,
      active = excluded.active;
