-- Migration 005 (Postgres): RAG company — chunk đại lý dùng chung, tách
-- khỏi corpus persona.
ALTER TABLE rag_chunks
  ADD COLUMN IF NOT EXISTS domain VARCHAR(32) NOT NULL DEFAULT 'persona';
ALTER TABLE rag_chunks ALTER COLUMN persona_id DROP NOT NULL;
