-- Migration 008 (SQLite): gpt-review-fixes — contact state + tenant + branch.
-- Chạy 1 lần cho DB đã có; DB mới đã có schema này qua
-- Base.metadata.create_all. SQLite không có ADD COLUMN IF NOT EXISTS nên
-- cột mới dùng ADD COLUMN thường (cột nullable, chạy lại sẽ báo lỗi
-- "duplicate column name" — bỏ qua lỗi đó nếu cột đã tồn tại).
-- SQLite không hỗ trợ ADD COLUMN kèm REFERENCES nên parent_message_id
-- thêm không có FK (DB mới qua create_all có FK; SQLite mặc định
-- không enforce FK nên hành vi tương đương).
CREATE TABLE IF NOT EXISTS contact_states (
  id VARCHAR(32) PRIMARY KEY,
  thread_id VARCHAR(32) NOT NULL,
  actor_id VARCHAR(64) NOT NULL,
  purchase_interest VARCHAR(32),
  contact_permission VARCHAR(32),
  conversation_status VARCHAR(32),
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_contact_thread_actor UNIQUE (thread_id, actor_id)
);
ALTER TABLE threads ADD COLUMN tenant VARCHAR(64);
ALTER TABLE messages ADD COLUMN parent_message_id VARCHAR(32);
