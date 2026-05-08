-- Proper Noun Normalization Staging Table
-- Allows review of AI-suggested name normalizations before applying

CREATE TABLE IF NOT EXISTS public.proper_noun_staging (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id TEXT NOT NULL,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('Account', 'Contact', 'Lead', 'Opportunity')),
    record_id UUID NOT NULL,
    current_value TEXT NOT NULL,
    proposed_value TEXT NOT NULL,
    confidence_score NUMERIC(3,2) NOT NULL DEFAULT 0.5,
    detection_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    context_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    ai_analysis JSONB DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending_review' 
        CHECK (status IN ('pending_review', 'approved', 'rejected', 'applied', 'skipped')),
    applied_at TIMESTAMPTZ,
    applied_by TEXT,
    rejection_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_proper_noun_staging_batch ON public.proper_noun_staging(batch_id);
CREATE INDEX IF NOT EXISTS idx_proper_noun_staging_status ON public.proper_noun_staging(status);
CREATE INDEX IF NOT EXISTS idx_proper_noun_staging_entity ON public.proper_noun_staging(entity_type, record_id);
CREATE INDEX IF NOT EXISTS idx_proper_noun_staging_created ON public.proper_noun_staging(created_at DESC);

-- Trigger to update updated_at
CREATE OR REPLACE FUNCTION update_proper_noun_staging_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_proper_noun_staging_updated_at ON public.proper_noun_staging;
CREATE TRIGGER trg_proper_noun_staging_updated_at
    BEFORE UPDATE ON public.proper_noun_staging
    FOR EACH ROW
    EXECUTE FUNCTION update_proper_noun_staging_updated_at();

-- RLS Policies
ALTER TABLE public.proper_noun_staging ENABLE ROW LEVEL SECURITY;

-- Allow service role full access
CREATE POLICY "Service role has full access" ON public.proper_noun_staging
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Allow authenticated users to read and approve/reject
CREATE POLICY "Authenticated users can view staging" ON public.proper_noun_staging
    FOR SELECT
    TO authenticated
    USING (true);

CREATE POLICY "Authenticated users can insert staging" ON public.proper_noun_staging
    FOR INSERT
    TO authenticated
    WITH CHECK (true);

CREATE POLICY "Authenticated users can update status" ON public.proper_noun_staging
    FOR UPDATE
    TO authenticated
    USING (true)
    WITH CHECK (status IN ('approved', 'rejected', 'applied', 'skipped'));

-- View for pending reviews
CREATE OR REPLACE VIEW public.vw_pending_normalizations AS
SELECT 
    id,
    batch_id,
    entity_type,
    record_id,
    current_value,
    proposed_value,
    confidence_score,
    detection_reasons,
    context_data,
    ai_analysis,
    created_at
FROM public.proper_noun_staging
WHERE status = 'pending_review'
ORDER BY confidence_score DESC, created_at ASC;

-- Function to bulk approve normalizations
CREATE OR REPLACE FUNCTION public.approve_normalization_batch(
    p_batch_id TEXT,
    p_approved_by TEXT DEFAULT NULL
)
RETURNS TABLE(
    approved_count INTEGER,
    rejected_count INTEGER
) AS $$
DECLARE
    v_approved INTEGER := 0;
    v_rejected INTEGER := 0;
BEGIN
    -- Auto-approve high confidence (>0.85) items
    UPDATE public.proper_noun_staging
    SET 
        status = 'approved',
        ai_analysis = ai_analysis || '{"auto_approved": true}'::jsonb,
        updated_at = NOW()
    WHERE batch_id = p_batch_id 
      AND confidence_score >= 0.85
      AND status = 'pending_review';
    
    GET DIAGNOSTICS v_approved = ROW_COUNT;
    
    -- Mark low confidence (<0.6) for manual review
    UPDATE public.proper_noun_staging
    SET 
        status = 'pending_review',
        ai_analysis = ai_analysis || '{"needs_manual_review": true}'::jsonb,
        updated_at = NOW()
    WHERE batch_id = p_batch_id 
      AND confidence_score < 0.6
      AND status = 'pending_review';
    
    GET DIAGNOSTICS v_rejected = ROW_COUNT;
    
    RETURN QUERY SELECT v_approved, v_rejected;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

COMMENT ON TABLE public.proper_noun_staging IS 
    'Staging table for AI-assisted proper noun normalization suggestions';
COMMENT ON COLUMN public.proper_noun_staging.detection_reasons IS 
    'Array of reasons why this record was flagged: ALL_CAPS, all_lowercase, spacing_issues, mixed_case_anomaly';
COMMENT ON COLUMN public.proper_noun_staging.context_data IS 
    'Additional context like accountType, email, etc.';
COMMENT ON COLUMN public.proper_noun_staging.ai_analysis IS 
    'OpenAI reasoning about edge cases and recommendations';
