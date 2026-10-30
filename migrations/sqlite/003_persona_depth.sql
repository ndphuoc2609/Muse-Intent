-- Migration 003 (SQLite): như bản Postgres, JSON lưu dạng TEXT.
-- SQLite không có ADD COLUMN IF NOT EXISTS: chạy từng ALTER, bỏ qua
-- lỗi "duplicate column name" nếu chạy lại. Server tự tạo bảng mới qua
-- create_all; với DB đã có, file này là bắt buộc để thêm cột mới.
CREATE TABLE IF NOT EXISTS persona_profiles (
  persona_id VARCHAR(64) PRIMARY KEY REFERENCES personas(id),
  occupation VARCHAR(256), daily_rhythm TEXT, family_structure TEXT,
  ownership_status VARCHAR(64), purchase_context TEXT,
  spending_limit_note TEXT, version INTEGER DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS persona_perspectives (
  id VARCHAR(32) PRIMARY KEY, persona_id VARCHAR(64) NOT NULL REFERENCES personas(id),
  aspect VARCHAR(64) NOT NULL, content TEXT NOT NULL, prior_content TEXT,
  status VARCHAR(32) DEFAULT 'active', version INTEGER DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS persona_style_guides (
  persona_id VARCHAR(64) PRIMARY KEY REFERENCES personas(id),
  address_form VARCHAR(128), answer_length VARCHAR(64), intimacy VARCHAR(64),
  emoji_use VARCHAR(64), storytelling TEXT, proactive_question_level VARCHAR(64),
  avoid_patterns TEXT DEFAULT '[]', style_examples TEXT DEFAULT '[]',
  version INTEGER DEFAULT 1, created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS persona_categories (
  id VARCHAR(32) PRIMARY KEY, persona_id VARCHAR(64) NOT NULL REFERENCES personas(id),
  category_code VARCHAR(64) NOT NULL, status VARCHAR(32) DEFAULT 'active',
  version INTEGER DEFAULT 1, created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_persona_category UNIQUE (persona_id, category_code)
);
CREATE TABLE IF NOT EXISTS tag_definitions (
  tag VARCHAR(128) PRIMARY KEY, namespace VARCHAR(64) NOT NULL,
  definition TEXT NOT NULL, applies_when TEXT NOT NULL,
  subject_scope VARCHAR(128) NOT NULL, version INTEGER DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS tag_assignments (
  id VARCHAR(32) PRIMARY KEY, entity_type VARCHAR(32) NOT NULL,
  entity_id VARCHAR(64) NOT NULL, tag VARCHAR(128) NOT NULL REFERENCES tag_definitions(tag),
  source VARCHAR(32) DEFAULT 'seed', status VARCHAR(32) DEFAULT 'active',
  version INTEGER DEFAULT 1, created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_tag_assignment UNIQUE (entity_type, entity_id, tag)
);
CREATE TABLE IF NOT EXISTS enrichment_drafts (
  id VARCHAR(32) PRIMARY KEY, persona_id VARCHAR(64) NOT NULL REFERENCES personas(id),
  kind VARCHAR(32) NOT NULL, payload TEXT NOT NULL, reason TEXT,
  anchor_refs TEXT DEFAULT '[]', proposed_tags TEXT DEFAULT '[]',
  proposed_categories TEXT DEFAULT '[]', conditions TEXT DEFAULT '[]',
  conflicts TEXT DEFAULT '[]', source VARCHAR(32) DEFAULT 'generated',
  status VARCHAR(32) DEFAULT 'draft', validator_errors TEXT DEFAULT '[]',
  version INTEGER DEFAULT 1, created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS story_links (
  id VARCHAR(32) PRIMARY KEY,
  from_event_id VARCHAR(64) NOT NULL REFERENCES persona_stories(event_id),
  to_event_id VARCHAR(64) NOT NULL REFERENCES persona_stories(event_id),
  link_type VARCHAR(32) NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_story_link UNIQUE (from_event_id, to_event_id, link_type)
);
CREATE TABLE IF NOT EXISTS retrieval_logs (
  id VARCHAR(32) PRIMARY KEY, persona_id VARCHAR(64) NOT NULL,
  thread_id VARCHAR(32) NOT NULL, actor_id VARCHAR(64) NOT NULL,
  entity_type VARCHAR(32) NOT NULL, entity_id VARCHAR(64) NOT NULL,
  reason TEXT, score FLOAT, rank INTEGER,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE persona_stories ADD COLUMN participants TEXT DEFAULT '[]';
ALTER TABLE persona_stories ADD COLUMN context_text TEXT;
ALTER TABLE persona_stories ADD COLUMN concern_or_choice TEXT;
ALTER TABLE persona_stories ADD COLUMN action_text TEXT;
ALTER TABLE persona_stories ADD COLUMN outcome_text TEXT;
ALTER TABLE persona_stories ADD COLUMN feeling_or_lesson TEXT;
ALTER TABLE persona_stories ADD COLUMN canonical_facts TEXT DEFAULT '[]';
ALTER TABLE persona_stories ADD COLUMN tellable_policy VARCHAR(32);
ALTER TABLE persona_stories ADD COLUMN related_need_topics TEXT DEFAULT '[]';
ALTER TABLE persona_stories ADD COLUMN story_categories TEXT DEFAULT '[]';
ALTER TABLE customer_facts ADD COLUMN need_kind VARCHAR(64);
ALTER TABLE customer_facts ADD COLUMN certainty VARCHAR(32);
