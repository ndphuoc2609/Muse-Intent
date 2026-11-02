-- Migration 004 (SQLite): RAG persona-only — bảng chunk vector + meta index.
-- Server tự tạo bảng mới qua Base.metadata.create_all khi khởi động với
-- DB mới; với DB đã có, file này là bắt buộc.
CREATE TABLE IF NOT EXISTS rag_chunks (
  id VARCHAR(160) PRIMARY KEY,
  persona_id VARCHAR(64) NOT NULL REFERENCES personas(id),
  chunk_type VARCHAR(32) NOT NULL,
  source_ref VARCHAR(160) NOT NULL,
  text TEXT NOT NULL,
  embedding TEXT NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS rag_index_meta (
  key VARCHAR(64) PRIMARY KEY,
  value TEXT NOT NULL
);
