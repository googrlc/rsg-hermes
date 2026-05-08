-- updated_at triggers on sync_mappings + outbound_sync_queue (matches repo 20260507010000 tail).

DROP TRIGGER IF EXISTS hermes_touch_updated_at_sync_mappings ON public.sync_mappings;
CREATE TRIGGER hermes_touch_updated_at_sync_mappings
  BEFORE UPDATE ON public.sync_mappings
  FOR EACH ROW EXECUTE FUNCTION public.hermes_touch_updated_at();

DROP TRIGGER IF EXISTS hermes_touch_updated_at_outbound_sync_queue ON public.outbound_sync_queue;
CREATE TRIGGER hermes_touch_updated_at_outbound_sync_queue
  BEFORE UPDATE ON public.outbound_sync_queue
  FOR EACH ROW EXECUTE FUNCTION public.hermes_touch_updated_at();
