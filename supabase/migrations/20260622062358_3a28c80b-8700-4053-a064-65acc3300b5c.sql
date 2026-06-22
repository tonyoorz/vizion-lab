
DROP POLICY IF EXISTS "authenticated read knowledge" ON public.knowledge_chunks;
DROP POLICY IF EXISTS "authenticated write knowledge" ON public.knowledge_chunks;
DROP POLICY IF EXISTS "authenticated update knowledge" ON public.knowledge_chunks;
DROP POLICY IF EXISTS "authenticated delete knowledge" ON public.knowledge_chunks;

REVOKE ALL ON public.knowledge_chunks FROM authenticated;
REVOKE EXECUTE ON FUNCTION public.match_knowledge(vector, text, int, text[], jsonb) FROM authenticated, PUBLIC;

ALTER FUNCTION public.match_knowledge(vector, text, int, text[], jsonb) SECURITY INVOKER;
