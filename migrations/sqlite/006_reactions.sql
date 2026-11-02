-- Migration 006 (SQLite): reactions kiểu Facebook cho comment.
CREATE TABLE IF NOT EXISTS message_reactions (
  id VARCHAR(32) PRIMARY KEY,
  message_id VARCHAR(32) NOT NULL REFERENCES messages(id),
  actor_id VARCHAR(64) NOT NULL,
  emoji VARCHAR(16) NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_reaction_message_actor UNIQUE (message_id, actor_id)
);
