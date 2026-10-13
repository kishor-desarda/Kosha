# SPDX-License-Identifier: AGPL-3.0-or-later
"""The AI engine's log (specification 14.2 and 14.6): one row per call to an AI provider with the job, model,
person, tokens, time and cost, never the content. It feeds the monthly budget, its alerts and the usage table.

Written to run safely twice (the PostgreSQL copy tool re-applies changes on emptied tables)."""

SQL = """
CREATE TABLE IF NOT EXISTS ai_calls (
  id INTEGER PRIMARY KEY,
  suggestion_id INTEGER,
  job TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  user_id INTEGER,
  prompt_tokens INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  ms INTEGER NOT NULL DEFAULT 0,
  cost REAL NOT NULL DEFAULT 0,
  ok INTEGER NOT NULL DEFAULT 1,
  fallback INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ai_calls_at ON ai_calls(at);
"""
