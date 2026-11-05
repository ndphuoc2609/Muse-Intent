-- Migration 007 (SQLite): review fixes — chunk versioning + lead terminal state.
-- Chạy 1 lần cho DB đã có; DB mới đã có schema này qua
-- Base.metadata.create_all. SQLite không có ADD COLUMN IF NOT EXISTS nên
-- cột mới dùng ADD COLUMN thường (cột nullable, chạy lại sẽ báo lỗi
-- "duplicate column name" — bỏ qua lỗi đó nếu cột đã tồn tại).
ALTER TABLE rag_chunks ADD COLUMN source_sha VARCHAR(64);
CREATE TABLE IF NOT EXISTS lead_states (
  id VARCHAR(32) PRIMARY KEY,
  thread_id VARCHAR(32) NOT NULL,
  actor_id VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_lead_thread_actor UNIQUE (thread_id, actor_id)
);
