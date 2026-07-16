-- Structural backstop for the canonical book (feeds the renewal-eligibility engine).
--
-- The canonical tables were CSV-loaded 2026-06-10 and drifted from the phase-0
-- migration (20260714200000): the live canonical_policies is keyed by policy_guid
-- and carries duplicate policy_number groups, so the intended
-- uq_canonical_policies_number was never in force. The new NowCerts→canonical sync
-- (`hermes --sync-canonical-book`) collapses those duplicates to one keeper per
-- policy_number as it reconciles.
--
-- APPLY ORDER: run a full `hermes --sync-canonical-book` FIRST (it removes the
-- duplicate rows). THEN apply this migration to lock the 1-row-per-key invariant
-- structurally. These are pure additive CREATE UNIQUE INDEX statements — no data
-- is deleted here; if duplicates still remain the index creation will error, which
-- is the intended signal that the collapsing sync has not completed successfully.

-- One current row per policy_number.
CREATE UNIQUE INDEX IF NOT EXISTS uq_canonical_policies_number
    ON public.canonical_policies (policy_number);

-- One canonical client per NowCerts insured (partial: real GUIDs only).
CREATE UNIQUE INDEX IF NOT EXISTS uq_canonical_clients_insured_guid
    ON public.canonical_clients (nowcerts_insured_guid)
    WHERE nowcerts_insured_guid IS NOT NULL;

-- One mirror row per NowCerts insured GUID.
CREATE UNIQUE INDEX IF NOT EXISTS uq_nowcerts_insured_mirror_guid
    ON public.nowcerts_insured_mirror (insured_guid);
