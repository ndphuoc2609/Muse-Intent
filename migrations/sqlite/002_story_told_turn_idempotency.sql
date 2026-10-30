-- Migration 002 (SQLite): bảng mới cho branch improve/claim-history-idempotency.
-- Idempotent: CREATE TABLE IF NOT EXISTS. Server tự tạo bảng thiếu qua
-- Base.metadata.create_all khi khởi động; file này chỉ để review / chạy thủ công.
CREATE TABLE IF NOT EXISTS story_told (
  id VARCHAR(32) PRIMARY KEY,
  event_id VARCHAR(64) REFERENCES persona_stories(event_id),
  persona_id VARCHAR(64) NOT NULL,
  actor_id VARCHAR(64) NOT NULL,
  thread_id VARCHAR(32) NOT NULL,
  message_id VARCHAR(32) REFERENCES messages(id),
  told_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_story_told_event_actor_message
    UNIQUE (event_id, actor_id, message_id)
);
CREATE TABLE IF NOT EXISTS turn_idempotency (
  id VARCHAR(32) PRIMARY KEY,
  thread_id VARCHAR(32) NOT NULL,
  actor_id VARCHAR(64) NOT NULL,
  idempotency_key VARCHAR(128) NOT NULL,
  request_hash VARCHAR(64) NOT NULL,
  user_message_id VARCHAR(32),
  persona_message_id VARCHAR(32),
  status VARCHAR(32) DEFAULT 'in_progress',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_turn_idem_scope
    UNIQUE (thread_id, actor_id, idempotency_key)
);
-- messages.idempotency_key UNIQUE toàn cục vẫn giữ trong DB cũ, nhưng
-- code mới để NULL ở cột đó và theo dõi qua turn_idempotency, nên
-- không cần rebuild bảng messages để chạy branch này.
