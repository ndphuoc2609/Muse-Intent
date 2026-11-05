-- Migration 007 (Postgres): review fixes — chunk versioning + lead terminal state.
-- Idempotent: ADD COLUMN IF NOT EXISTS + CREATE TABLE IF NOT EXISTS.
-- DB mới đã có schema này qua Base.metadata.create_all; file này dành
-- cho DB đã có (Neon) khi pull code mới về mà chưa restart server.
ALTER TABLE rag_chunks
  ADD COLUMN IF NOT EXISTS source_sha VARCHAR(64);
CREATE TABLE IF NOT EXISTS lead_states (
  id VARCHAR(32) PRIMARY KEY,
  thread_id VARCHAR(32) NOT NULL,
  actor_id VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_lead_thread_actor UNIQUE (thread_id, actor_id)
);
