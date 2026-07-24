-- Repair: restore canonical_policies rows that --sync-canonical-book wrongly
-- tombstoned as "Inactive: not in NowCerts".
--
-- Built 2026-07-24. Every guid below was verified PRESENT in the live NowCerts
-- book (full fetch, 439 policies) by policy_guid before being included; the
-- status/active/date/premium values are the live AMS values, not invented.
--
-- Of the 95 tombstoned rows: 47 were live (restored here), 48 are genuinely
-- gone and are deliberately NOT touched.
--
-- Effect: 47 rows back to active=true, 26 of them re-entering the 120-day
-- renewal window, $136,849.98 of premium restored to the renewal desk.
--
-- Premium is applied via coalesce so a NULL from the AMS never wipes an
-- existing value. Re-running is harmless (idempotent).
--
-- After running, rebuild the desk:
--   docker compose run --rm hermes hermes --renewal-refresh

-- Casts are required: a VALUES list types these as text/float8, while the
-- columns are date/numeric.
update canonical_policies c set
  status = v.status, active = v.active,
  effective_date = v.eff::date, expiration_date = v.exp::date,
  current_term_amount = coalesce(v.prem::numeric, c.current_term_amount),
  premium_amount      = coalesce(v.prem::numeric, c.premium_amount),
  annualized_premium  = coalesce(v.prem::numeric, c.annualized_premium)
from (values
  ('a81763db-dde7-441c-9a89-1245ebb576a4', 'Active', true, '2026-08-15', '2027-02-15', 3213.0),
  ('33adab3b-52aa-4183-951c-4bfe5e21cc69', 'Active', true, '2026-04-17', '2026-10-17', 1350.0),
  ('60253b7b-b4b8-4221-9d02-3d09113fd4ea', 'Active', true, '2025-09-01', '2026-09-01', 0.0),
  ('b0c36206-20e3-4ecd-b001-4160ddf190d4', 'Active', true, '2026-05-03', '2026-11-03', 4090.0),
  ('32405818-de56-4399-9a57-76f343868538', 'Active', true, '2026-03-03', '2026-09-03', 3226.0),
  ('a630e774-d8ea-4456-a00c-0a3a2745d451', 'Active', true, '2025-11-10', '2026-11-10', 4417.0),
  ('a57f1424-93c4-4478-95b2-beab133c3a8b', 'Active', true, '2025-08-05', '2026-08-05', 20939.8),
  ('41a07183-d4f3-4656-b34b-0030b925e556', 'Active', true, '2026-02-18', '2026-08-18', 2587.0),
  ('a34227ff-b746-4258-aae1-3f0ea1ae9c95', 'Active', true, '2025-11-25', '2026-11-25', 73000.0),
  ('27e87748-690d-47cc-894b-361fd47faa71', 'Active', true, '2025-11-10', '2026-11-10', 3342.0),
  ('6d5850bf-7718-49c8-a5e7-2e91e9d87b7a', 'Active', true, '2025-08-07', '2026-08-07', 18866.09),
  ('3f8619f7-f6d6-456d-9b98-1bb8ab26f357', 'Active', true, '2026-04-18', '2026-10-18', 2377.0),
  ('01cbf4f7-d789-47a8-86fd-2433a2072eda', 'Active', true, '2026-05-18', '2027-05-18', 8888.0),
  ('788c7751-3ba0-433d-9c0d-75ddfff0cb74', 'Active', true, '2025-12-26', '2026-12-26', 2846.0),
  ('772c4995-0a66-41d0-a9fc-e5239c37d778', 'Active', true, '2025-09-15', '2026-09-15', 21441.09),
  ('8947b0f2-0107-4b27-bd6b-3f3fc64b3efb', 'Active', true, '2026-08-06', '2027-02-06', 1294.0),
  ('decc2498-8d25-47ec-be40-c5370153cfab', 'Active', true, '2026-03-25', '2026-09-25', 839.0),
  ('2bfa3af2-1834-4d6f-991c-3af45ce8a694', 'Active', true, '2026-04-17', '2026-10-17', 1010.0),
  ('a6950794-65c4-4e0a-a227-a4b9d685561f', 'Active', true, '2026-06-17', '2026-12-17', 3145.0),
  ('f129fc89-55da-480c-ab02-7299a54a85ec', 'Active', true, '2026-06-10', '2026-12-10', 2067.0),
  ('36b0c898-c9c7-4aa3-9a51-e498509f2507', 'Expired', true, '2025-12-13', '2026-06-13', 1421.0),
  ('3973568c-c173-4d42-9a89-f93690e75795', 'Active', true, '2026-06-27', '2026-12-27', 2557.0),
  ('bcf196ea-e41f-4d13-9b59-6380503150df', 'Active', true, '2026-06-13', '2026-12-13', 1901.0),
  ('643bc814-2acc-4860-9a8e-d98acca0d7e1', 'Active', true, '2026-07-15', '2027-01-15', 3970.0),
  ('9ed85082-425c-40bc-9d17-b58208a1b02d', 'Expired', true, '2025-12-13', '2026-06-13', 1891.0),
  ('6c91b5ec-3de7-4718-82b5-ef36f4a50aa2', 'Active', true, '2026-07-05', '2027-01-05', 1292.0),
  ('ad2f30e1-65a4-43f8-95c5-f9a2aeb3da8f', 'Active', true, '2026-06-26', '2026-12-26', 3104.0),
  ('fc02f878-00ce-4525-8462-dad9238ef698', 'Active', true, '2026-06-13', '2026-12-13', 1423.0),
  ('7fc2b883-d821-4866-9e64-1ef2379820f9', 'Active', true, '2026-04-18', '2026-10-18', 1831.0),
  ('9a8f37bf-09c4-4a93-b1ed-ab3be646f2ef', 'Active', true, '2026-05-20', '2026-11-20', 2967.0),
  ('d1292826-f376-4a93-a219-ffdb2336b00d', 'Active', true, '2026-02-15', '2026-08-15', 2652.0),
  ('8141d513-44be-47db-99fa-586473c10ad6', 'Active', true, '2026-04-17', '2026-10-17', 4207.0),
  ('6061cd4f-e48b-48b7-bbb1-f1596dfffbb4', 'Active', true, '2026-02-28', '2026-08-28', 1453.0),
  ('df0c0089-892f-406a-b11a-2319e41f0c54', 'Active', true, '2026-03-27', '2027-03-27', 2095.0),
  ('d0cda7b8-89dc-4846-bf18-63f81effdba9', 'Active', true, '2026-01-25', '2026-07-25', 659.0),
  ('48b52aab-4c75-416d-889e-b8b69bc6da75', 'Active', true, '2026-01-27', '2026-07-27', 930.0),
  ('c4509158-8c4d-421f-8c21-1e298f2114b5', 'Active', true, '2025-11-11', '2026-11-11', 3540.0),
  ('ecf63be1-29b2-4b7e-b16a-b417b1058dc5', 'Active', true, '2025-11-06', '2026-11-06', 20562.0),
  ('12813da3-6b76-4bcf-9481-8399729d1f6e', 'Active', true, '2026-07-17', '2027-01-14', 1332.0),
  ('5efd967b-6f34-4b8b-85d0-a4c836358dd2', 'Active', true, '2026-04-26', '2027-04-26', 485.0),
  ('915250a7-cebb-4453-a9b2-ef1c78cf3fd6', 'Active', true, '2026-03-15', '2026-09-15', 4857.0),
  ('11c0c95f-98cf-46bd-871f-4cad462b50f0', 'Active', true, '2026-04-01', '2026-10-01', 2605.0),
  ('6725fba9-dc99-48bf-bf48-adb659090ba1', 'Active', true, '2026-03-30', '2026-09-30', 1065.0),
  ('cd704fb7-7e16-4c0c-ad75-daeb8db13f64', 'Active', true, '2026-05-25', '2026-11-25', 2424.0),
  ('35326d2f-73c0-4e6c-bc4c-58843ee78928', 'Active', true, '2026-04-14', '2026-10-14', 5037.0),
  ('e4ef329f-a6a3-45cd-84fe-f3596204abab', 'Active', true, '2026-07-27', '2027-01-27', 917.0),
  ('fb702958-a053-4706-8550-a1b56d44584a', 'Active', true, '2026-07-17', '2027-01-17', 525.0)
) as v(guid, status, active, eff, exp, prem)
where c.policy_guid = v.guid;
