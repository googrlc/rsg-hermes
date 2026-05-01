# Hermes Supabase Migrations

These migrations define the Hermes AI Operations Center schema. They are
mirrored from [Supabase-Central](https://github.com/googrlc/Supabase-Central)
and have been applied to the production Supabase project.

## Migration order

1. `20260501131246_hermes_ai_master_schema.sql` — Tables, enums, indexes, RLS
2. `20260501144500_hermes_service_role_rls.sql` — Refined RLS for `service_role`
3. `20260501153000_hermes_edge_cases_hardening.sql` — FK ergonomics, triggers, constraints

## Applying locally

```bash
npx supabase@latest db reset --yes
```
