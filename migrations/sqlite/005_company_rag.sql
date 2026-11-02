-- Migration 005 (SQLite): RAG company — chunk đại lý dùng chung, tách
-- khỏi corpus persona. SQLite không sửa được NOT NULL của cột có sẵn
-- nên rebuild bảng: tạo bảng mới (domain + persona_id nullable), copy,
-- thay bảng cũ.
CREATE TABLE rag_chunks_new (
  id VARCHAR(160) PRIMARY KEY,
  persona_id VARCHAR(64) REFERENCES personas(id),
  domain VARCHAR(32) NOT NULL DEFAULT 'persona',
  chunk_type VARCHAR(32) NOT NULL,
  source_ref VARCHAR(160) NOT NULL,
  text TEXT NOT NULL,
  embedding TEXT NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO rag_chunks_new
  (id, persona_id, domain, chunk_type, source_ref, text, embedding,
   created_at)
SELECT id, persona_id, 'persona', chunk_type, source_ref, text, embedding,
  created_at FROM rag_chunks;
DROP TABLE rag_chunks;
ALTER TABLE rag_chunks_new RENAME TO rag_chunks;
