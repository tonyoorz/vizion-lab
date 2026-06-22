
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE public.knowledge_chunks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_type text NOT NULL,
  source_id text NOT NULL,
  chunk_index int NOT NULL DEFAULT 0,
  title text,
  content text NOT NULL,
  content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(title,'') || ' ' || content)) STORED,
  embedding vector(1536),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  model_version text NOT NULL DEFAULT 'google/gemini-embedding-001@1536',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_type, source_id, chunk_index)
);

GRANT SELECT, INSERT, UPDATE, DELETE ON public.knowledge_chunks TO authenticated;
GRANT ALL ON public.knowledge_chunks TO service_role;

ALTER TABLE public.knowledge_chunks ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated read knowledge"
  ON public.knowledge_chunks FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated write knowledge"
  ON public.knowledge_chunks FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY "authenticated update knowledge"
  ON public.knowledge_chunks FOR UPDATE TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "authenticated delete knowledge"
  ON public.knowledge_chunks FOR DELETE TO authenticated USING (true);

CREATE INDEX knowledge_chunks_embedding_idx
  ON public.knowledge_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX knowledge_chunks_tsv_idx
  ON public.knowledge_chunks USING gin (content_tsv);
CREATE INDEX knowledge_chunks_source_type_idx
  ON public.knowledge_chunks (source_type);
CREATE INDEX knowledge_chunks_metadata_idx
  ON public.knowledge_chunks USING gin (metadata jsonb_path_ops);

CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

CREATE TRIGGER knowledge_chunks_set_updated_at
  BEFORE UPDATE ON public.knowledge_chunks
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

CREATE OR REPLACE FUNCTION public.match_knowledge(
  query_embedding vector(1536),
  query_text text,
  match_count int DEFAULT 8,
  source_types text[] DEFAULT NULL,
  filter jsonb DEFAULT '{}'::jsonb
)
RETURNS TABLE (
  id uuid,
  source_type text,
  source_id text,
  title text,
  content text,
  metadata jsonb,
  vec_score double precision,
  kw_score double precision,
  rrf_score double precision
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  k constant int := 60;
  pool constant int := greatest(match_count * 5, 40);
BEGIN
  RETURN QUERY
  WITH vec AS (
    SELECT c.id,
           1 - (c.embedding <=> query_embedding) AS score,
           row_number() OVER (ORDER BY c.embedding <=> query_embedding) AS rnk
    FROM public.knowledge_chunks c
    WHERE c.embedding IS NOT NULL
      AND (source_types IS NULL OR c.source_type = ANY(source_types))
      AND (filter = '{}'::jsonb OR c.metadata @> filter)
    ORDER BY c.embedding <=> query_embedding
    LIMIT pool
  ),
  kw AS (
    SELECT c.id,
           ts_rank_cd(c.content_tsv, plainto_tsquery('simple', coalesce(query_text,''))) AS score,
           row_number() OVER (ORDER BY ts_rank_cd(c.content_tsv, plainto_tsquery('simple', coalesce(query_text,''))) DESC) AS rnk
    FROM public.knowledge_chunks c
    WHERE (source_types IS NULL OR c.source_type = ANY(source_types))
      AND (filter = '{}'::jsonb OR c.metadata @> filter)
      AND (coalesce(query_text,'') = '' OR c.content_tsv @@ plainto_tsquery('simple', query_text))
    ORDER BY score DESC
    LIMIT pool
  ),
  merged AS (
    SELECT coalesce(vec.id, kw.id) AS id,
           coalesce(vec.score, 0)::double precision AS vec_score,
           coalesce(kw.score, 0)::double precision AS kw_score,
           (coalesce(1.0 / (k + vec.rnk), 0) + coalesce(1.0 / (k + kw.rnk), 0))::double precision AS rrf_score
    FROM vec FULL OUTER JOIN kw ON vec.id = kw.id
  )
  SELECT c.id, c.source_type, c.source_id, c.title, c.content, c.metadata,
         m.vec_score, m.kw_score, m.rrf_score
  FROM merged m
  JOIN public.knowledge_chunks c ON c.id = m.id
  ORDER BY m.rrf_score DESC
  LIMIT match_count;
END;
$$;

GRANT EXECUTE ON FUNCTION public.match_knowledge(vector, text, int, text[], jsonb) TO authenticated, service_role;
