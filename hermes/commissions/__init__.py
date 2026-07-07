"""Commission ingest: NowCerts policies -> Supabase commission_ledger.

Phase 2 of the Commission Command build spec. A nightly job pulls policies from
NowCerts (READ-ONLY), computes the *expected* commission from the
``commission_rules`` rate catalog, and idempotently upserts a row into
``commission_ledger`` keyed on ``nowcerts_policy_id``.

HARD GATE: this must NOT run against live NowCerts until the ~4,000 glitched
duplicate policies are purged and Lamar confirms. Policies tagged
``PURGE-POLICY-2026-07`` are excluded permanently as a belt-and-suspenders filter.

Entry point: :func:`hermes.commissions.sweep.run`.
"""
