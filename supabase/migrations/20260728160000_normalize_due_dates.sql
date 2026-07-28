-- =====================================================================================
-- NORMALIZE CASE + TASK DUE DATES to the agency's close of business.
--
-- due_at is a timestamptz, but a due date is a DAY — nobody means "by 8:34:14pm".
-- Every write path put a different kind of value in it: `utcnow() + n days` with the
-- creation second still attached, a bare YYYY-MM-DD from the portal, and midnight UTC.
--
-- Midnight UTC is the damaging one: it is 8pm the PREVIOUS DAY in Eastern time, so
-- every live case opened "due tomorrow" has been displaying as due today. All eight
-- dated cases and all eight dated tasks are in that state.
--
-- The rule (hermes/core/due_dates.py) is 17:00 America/New_York — the end of the
-- business day, far enough from midnight in both directions that the date reads the
-- same in every timezone anyone here will ever use.
--
-- Picking the day to snap each row to:
--   * exactly midnight UTC → the date the writer INTENDED is the UTC date. These rows
--     are dates that were widened into timestamps; their ET date is a day early and
--     snapping to it would bake in the very bug being fixed.
--   * anything else → an actual moment someone worked at, so its Eastern date is the
--     day it belongs to.
-- =====================================================================================

CREATE OR REPLACE FUNCTION pg_temp.normalized_due(due timestamptz)
RETURNS timestamptz
LANGUAGE sql IMMUTABLE AS $$
    SELECT (
        CASE
            WHEN due AT TIME ZONE 'UTC' = date_trunc('day', due AT TIME ZONE 'UTC')
                THEN (due AT TIME ZONE 'UTC')::date
            ELSE (due AT TIME ZONE 'America/New_York')::date
        END + TIME '17:00'
    ) AT TIME ZONE 'America/New_York';
$$;

UPDATE public.agency_crm_cases
   SET due_at = pg_temp.normalized_due(due_at)
 WHERE due_at IS NOT NULL
   AND due_at IS DISTINCT FROM pg_temp.normalized_due(due_at);

UPDATE public.agency_crm_tasks
   SET due_at = pg_temp.normalized_due(due_at)
 WHERE due_at IS NOT NULL
   AND due_at IS DISTINCT FROM pg_temp.normalized_due(due_at);
