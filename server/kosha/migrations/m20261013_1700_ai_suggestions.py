# SPDX-License-Identifier: AGPL-3.0-or-later
"""AI assistance (optional): every request to the AI provider and what became of its suggestion.

ai_suggestions      one row per request: the feature (classify, changelog, ask), the record it is about, the
                    measured input it was built from (input_hash), what was sent (sent_json, shown to the admin),
                    the checked result, and the person's decision (accepted, edited, dismissed). A suggestion
                    never changes a record by itself; accepting it is a separate, audited step.
"""
SQL = """
CREATE TABLE IF NOT EXISTS ai_suggestions (
  id INTEGER PRIMARY KEY,
  dedupe_key TEXT NOT NULL,
  feature TEXT NOT NULL,
  subject TEXT NOT NULL,
  subject_id INTEGER,
  revision TEXT NOT NULL DEFAULT '',
  input_hash TEXT NOT NULL DEFAULT '',
  provider TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  sent_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT NOT NULL DEFAULT '{}',
  error TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL DEFAULT '',
  decision TEXT NOT NULL DEFAULT '',
  decided_by INTEGER,
  decided_at TEXT,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_ai_suggestions_key ON ai_suggestions(dedupe_key, status);
CREATE INDEX IF NOT EXISTS ix_ai_suggestions_subject ON ai_suggestions(subject, subject_id);
CREATE INDEX IF NOT EXISTS ix_ai_suggestions_user ON ai_suggestions(created_by, status);
"""
