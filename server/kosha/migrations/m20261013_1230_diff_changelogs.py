# SPDX-License-Identifier: AGPL-3.0-or-later
"""Measured change list and change-log drafts (spec 7.7).

changelogs          the change log an engineer saved for a document revision or a change order:
                    the text (sections_json, note) apart from the measured facts it was built from
                    (input_json, input_hash), so a later draft can be produced from the same facts.
changelog_drafts    drafts generated from an input: today the rule-based "rules-v1", later AI jobs
                    (generator "ai:<provider>", status pending / ready / failed).
diff_prop_snapshots item properties as they were when a document revision was released.
"""
SQL = """
CREATE TABLE IF NOT EXISTS changelogs (
  id INTEGER PRIMARY KEY,
  subject TEXT NOT NULL,
  subject_id INTEGER NOT NULL,
  revision TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  sections_json TEXT NOT NULL DEFAULT '{}',
  input_json TEXT NOT NULL DEFAULT '{}',
  input_hash TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'rules',
  created_by INTEGER,
  created_at TEXT NOT NULL,
  updated_by INTEGER,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_changelogs_subject ON changelogs(subject, subject_id, revision);

CREATE TABLE IF NOT EXISTS changelog_drafts (
  id INTEGER PRIMARY KEY,
  subject TEXT NOT NULL,
  subject_id INTEGER NOT NULL,
  revision TEXT NOT NULL DEFAULT '',
  generator TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  input_json TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  sections_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'ready',
  created_by INTEGER,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_changelog_drafts_subject ON changelog_drafts(subject, subject_id, revision);

CREATE TABLE IF NOT EXISTS diff_prop_snapshots (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL,
  revision TEXT NOT NULL,
  item_id INTEGER,
  props_json TEXT NOT NULL,
  taken_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_diff_prop_snapshots ON diff_prop_snapshots(document_id, revision);
"""
