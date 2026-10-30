-- Migration 002 (Postgres): bảng mới cho branch improve/claim-history-idempotency.
-- Idempotent: CREATE TABLE IF NOT EXISTS. Không sửa bảng cũ: status là
-- chuỗi nên 'pending'/'rejected' không cần thay đổi schema; app/models.py
-- là nguồn sự thật. Server tự tạo bảng thiếu qua Base.metadata.create_all
-- khi khởi động, file này chỉ để DBA review / chạy thủ công.
CREATE TABLE IF NOT EXISTS story_told (
  id VARCHAR(32) PRIMARY KEY,
  event_id VARCHAR(64) REFERENCES persona_stories(event_id),
  persona_id VARCHAR(64) NOT NULL,
  actor_id VARCHAR(64) NOT NULL,
  thread_id VARCHAR(32) NOT NULL,
  message_id VARCHAR(32) REFERENCES messages(id),
  told_at TIMESTAMPTZ DEFAULT now(),
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
  created_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_turn_idem_scope
    UNIQUE (thread_id, actor_id, idempotency_key)
);
-- Lưu ý: messages.idempotency_key UNIQUE toàn cục vẫn giữ trong DB cũ,
-- nhưng code mới để NULL ở cột đó và theo dõi idempotency qua bảng
-- turn_idempotency, nên không cần drop constraint để chạy branch này.
-- Drop constraint chỉ cần nếu muốn dọn legacy (Postgres):
--   ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_idempotency_key_key;
