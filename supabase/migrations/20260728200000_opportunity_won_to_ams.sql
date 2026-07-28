-- =====================================================================================
-- WON DEALS REACH THE AMS.
--
-- The rule the agency works to: the CRM is the working copy, NowCerts is the record of
-- what is REAL. A deal becomes real when it is WON — that is the moment the insured and
-- the policy have to exist in the system of record.
--
-- Today they mostly do not. The terminal writeback only fires for opportunities that
-- carry a nowcerts_opportunity_id — i.e. only ones that came FROM the AMS (14 of 64).
-- A cross-sell opened in the CRM on an existing client, or a converted lead, goes won
-- and NowCerts never hears about it. One of the three won deals on the book is in
-- exactly that state.
--
-- Two columns are needed to close that:
--
--   policy_number       — you cannot record a bound policy in the AMS without its
--                         number, and inventing one would put junk in the system of
--                         record. So it is captured on the deal when it is won, and
--                         the push refuses to run without it.
--   nowcerts_policy_guid — what came back, so the deal can be traced to the policy it
--                         became and the push is not repeated.
--
-- LOST deals get none of this. Nothing about a lost deal is written to NowCerts: it was
-- never coverage. It stays here with its x-date and lost reason, which is next year's
-- remarket list. (A deal MIRRORED from NowCerts still has its stage synced when lost —
-- that record already exists there and leaving it open forever is its own kind of lie.)
-- =====================================================================================

ALTER TABLE public.opportunities
    ADD COLUMN IF NOT EXISTS policy_number        TEXT,
    ADD COLUMN IF NOT EXISTS nowcerts_policy_guid TEXT,
    ADD COLUMN IF NOT EXISTS ams_pushed_at        TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS uq_opportunities_nowcerts_policy_guid
    ON public.opportunities (nowcerts_policy_guid)
    WHERE nowcerts_policy_guid IS NOT NULL;

COMMENT ON COLUMN public.opportunities.policy_number IS
    'The bound policy number. Required before a won deal can be pushed to NowCerts — a policy cannot be recorded in the AMS without one, and inventing one is not an option.';
COMMENT ON COLUMN public.opportunities.nowcerts_policy_guid IS
    'The NowCerts policy this deal became, set by the won-push executor. Its presence means the push already landed.';
COMMENT ON COLUMN public.opportunities.ams_pushed_at IS
    'When this won deal reached NowCerts. NULL on every open or lost deal — lost deals are never pushed.';
