-- =====================================================================================
-- HERMES FILES  (Command Center — Files side panel)
-- Purpose: durable store for files Hermes creates (notes, reports, save-lists,
-- proposals, saved answers) with full content, so the Command Center can list and
-- DOWNLOAD them. Distinct from hermes_documents (the Supermemory/Drive knowledge
-- index, which only keeps a preview).
-- Server-only: RLS ENABLED with NO policies — anon/authenticated blocked; hermes-api
-- reaches it via the service-role key.
-- =====================================================================================

CREATE TABLE IF NOT EXISTS public.hermes_files (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title        TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'note',          -- note|report|save-list|proposal|answer|other
    content      TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'text/markdown',
    file_ext     TEXT NOT NULL DEFAULT 'md',
    source       TEXT DEFAULT 'command-center',
    created_by   TEXT DEFAULT 'hermes',
    created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_hermes_files_created ON public.hermes_files(created_at DESC);

ALTER TABLE public.hermes_files ENABLE ROW LEVEL SECURITY;
