-- Migration 008 (Postgres): gpt-review-fixes — contact state + tenant + branch.
-- Idempotent: ADD COLUMN IF NOT EXISTS + CREATE TABLE IF NOT EXISTS.
-- DB mới đã có schema này qua Base.metadata.create_all; file này dành
-- cho DB đã có (Neon) khi pull code mới về mà chưa restart server.
CREATE TABLE IF NOT EXISTS contact_states (
  id VARCHAR(32) PRIMARY KEY,
  thread_id VARCHAR(32) NOT NULL,
  actor_id VARCHAR(64) NOT NULL,
  purchase_interest VARCHAR(32),
  contact_permission VARCHAR(32),
  conversation_status VARCHAR(32),
  created_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_contact_thread_actor UNIQUE (thread_id, actor_id)
);
ALTER TABLE threads
  ADD COLUMN IF NOT EXISTS tenant VARCHAR(64);
ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS parent_message_id VARCHAR(32)
  REFERENCES messages (id);
