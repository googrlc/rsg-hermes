-- =====================================================================================
-- THE INTAKE GATE IS A SOURCE.
--
-- rsg-cptintake (the intake gate) submits finished intakes to POST /api/intake: PDFs,
-- transcripts and operator-confirmed facts, synthesized against the crm-intake-writer
-- contract and enriched with cited reference-table codes. It has always been one of the
-- callers; it just had no name in this constraint, so its submissions could only land by
-- pretending to be 'manual_curl'.
--
-- Naming it is not cosmetic. It is the difference between "a human ran a curl" and "a
-- reviewed, cited intake came through the gate", and the pipeline needs to tell those
-- apart when it decides how much to trust a payload it did not extract itself.
--
-- Also folds in 'email-ms365' and 'email-gmail'. Those are already in the live
-- constraint and already writing rows — this keeps the migration history honest about
-- the constraint's actual contents rather than silently dropping two valid sources the
-- next time someone rebuilds from migrations.
-- =====================================================================================

ALTER TABLE public.intake_submissions
    DROP CONSTRAINT IF EXISTS intake_submissions_source_check;

ALTER TABLE public.intake_submissions
    ADD CONSTRAINT intake_submissions_source_check
    CHECK (source = ANY (ARRAY[
        'cowork'::text,
        'voice_tool'::text,
        'manual_curl'::text,
        'n8n'::text,
        'email-ms365'::text,
        'email-gmail'::text,
        'intake_gate'::text
    ]));

COMMENT ON COLUMN public.intake_submissions.source IS
    'Which caller submitted this intake. ''intake_gate'' is rsg-cptintake, whose payloads arrive already synthesized and cited — the worker uses them verbatim rather than re-extracting.';
