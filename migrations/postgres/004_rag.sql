-- Migration 004 (Postgres): RAG persona-only — bảng chunk vector + meta index.
-- Idempotent cho bảng mới (CREATE TABLE IF NOT EXISTS).
CREATE TABLE IF NOT EXISTS rag_chunks (
  id VARCHAR(160) PRIMARY KEY,
  persona_id VARCHAR(64) NOT NULL REFERENCES personas(id),
  chunk_type VARCHAR(32) NOT NULL,
  source_ref VARCHAR(160) NOT NULL,
  text TEXT NOT NULL,
  embedding TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS rag_index_meta (
  key VARCHAR(64) PRIMARY KEY,
  value TEXT NOT NULL
);
