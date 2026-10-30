-- Migration 003 (Postgres): persona-depth — profile/perspective/style,
-- category/tag taxonomy, enrichment draft, story links, retrieval log,
-- story episodic fields, customer need state.
-- Idempotent cho bảng mới (CREATE TABLE IF NOT EXISTS). Cột thêm vào
-- bảng cũ không có IF NOT EXISTS trong Postgres cũ, nên DBA chạy từng
-- lệnh ALTER và bỏ qua lỗi "already exists" nếu chạy lại. Server tự tạo
-- bảng/cột thiếu qua Base.metadata.create_all khi khởi động với DB mới;
-- với DB đã có, create_all KHÔNG thêm cột mới, nên file này là bắt buộc.
CREATE TABLE IF NOT EXISTS persona_profiles (
  persona_id VARCHAR(64) PRIMARY KEY REFERENCES personas(id),
  occupation VARCHAR(256), daily_rhythm TEXT, family_structure TEXT,
  ownership_status VARCHAR(64), purchase_context TEXT,
  spending_limit_note TEXT, version INTEGER DEFAULT 1,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS persona_perspectives (
  id VARCHAR(32) PRIMARY KEY, persona_id VARCHAR(64) NOT NULL REFERENCES personas(id),
  aspect VARCHAR(64) NOT NULL, content TEXT NOT NULL, prior_content TEXT,
  status VARCHAR(32) DEFAULT 'active', version INTEGER DEFAULT 1,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS persona_style_guides (
  persona_id VARCHAR(64) PRIMARY KEY REFERENCES personas(id),
  address_form VARCHAR(128), answer_length VARCHAR(64), intimacy VARCHAR(64),
  emoji_use VARCHAR(64), storytelling TEXT, proactive_question_level VARCHAR(64),
  avoid_patterns JSON DEFAULT '[]', style_examples JSON DEFAULT '[]',
  version INTEGER DEFAULT 1, created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS persona_categories (
  id VARCHAR(32) PRIMARY KEY, persona_id VARCHAR(64) NOT NULL REFERENCES personas(id),
  category_code VARCHAR(64) NOT NULL, status VARCHAR(32) DEFAULT 'active',
  version INTEGER DEFAULT 1, created_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_persona_category UNIQUE (persona_id, category_code)
);
CREATE TABLE IF NOT EXISTS tag_definitions (
  tag VARCHAR(128) PRIMARY KEY, namespace VARCHAR(64) NOT NULL,
  definition TEXT NOT NULL, applies_when TEXT NOT NULL,
  subject_scope VARCHAR(128) NOT NULL, version INTEGER DEFAULT 1,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tag_assignments (
  id VARCHAR(32) PRIMARY KEY, entity_type VARCHAR(32) NOT NULL,
  entity_id VARCHAR(64) NOT NULL, tag VARCHAR(128) NOT NULL REFERENCES tag_definitions(tag),
  source VARCHAR(32) DEFAULT 'seed', status VARCHAR(32) DEFAULT 'active',
  version INTEGER DEFAULT 1, created_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_tag_assignment UNIQUE (entity_type, entity_id, tag)
);
CREATE TABLE IF NOT EXISTS enrichment_drafts (
  id VARCHAR(32) PRIMARY KEY, persona_id VARCHAR(64) NOT NULL REFERENCES personas(id),
  kind VARCHAR(32) NOT NULL, payload JSON NOT NULL, reason TEXT,
  anchor_refs JSON DEFAULT '[]', proposed_tags JSON DEFAULT '[]',
  proposed_categories JSON DEFAULT '[]', conditions JSON DEFAULT '[]',
  conflicts JSON DEFAULT '[]', source VARCHAR(32) DEFAULT 'generated',
  status VARCHAR(32) DEFAULT 'draft', validator_errors JSON DEFAULT '[]',
  version INTEGER DEFAULT 1, created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS story_links (
  id VARCHAR(32) PRIMARY KEY,
  from_event_id VARCHAR(64) NOT NULL REFERENCES persona_stories(event_id),
  to_event_id VARCHAR(64) NOT NULL REFERENCES persona_stories(event_id),
  link_type VARCHAR(32) NOT NULL, created_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_story_link UNIQUE (from_event_id, to_event_id, link_type)
);
CREATE TABLE IF NOT EXISTS retrieval_logs (
  id VARCHAR(32) PRIMARY KEY, persona_id VARCHAR(64) NOT NULL,
  thread_id VARCHAR(32) NOT NULL, actor_id VARCHAR(64) NOT NULL,
  entity_type VARCHAR(32) NOT NULL, entity_id VARCHAR(64) NOT NULL,
  reason TEXT, score FLOAT, rank INTEGER,
  created_at TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE persona_stories ADD COLUMN IF NOT EXISTS participants JSON DEFAULT '[]';
ALTER TABLE persona_stories ADD COLUMN IF NOT EXISTS context_text TEXT;
ALTER TABLE persona_stories ADD COLUMN IF NOT EXISTS concern_or_choice TEXT;
ALTER TABLE persona_stories ADD COLUMN IF NOT EXISTS action_text TEXT;
ALTER TABLE persona_stories ADD COLUMN IF NOT EXISTS outcome_text TEXT;
ALTER TABLE persona_stories ADD COLUMN IF NOT EXISTS feeling_or_lesson TEXT;
ALTER TABLE persona_stories ADD COLUMN IF NOT EXISTS canonical_facts JSON DEFAULT '[]';
ALTER TABLE persona_stories ADD COLUMN IF NOT EXISTS tellable_policy VARCHAR(32);
ALTER TABLE persona_stories ADD COLUMN IF NOT EXISTS related_need_topics JSON DEFAULT '[]';
ALTER TABLE persona_stories ADD COLUMN IF NOT EXISTS story_categories JSON DEFAULT '[]';
ALTER TABLE customer_facts ADD COLUMN IF NOT EXISTS need_kind VARCHAR(64);
ALTER TABLE customer_facts ADD COLUMN IF NOT EXISTS certainty VARCHAR(32);
