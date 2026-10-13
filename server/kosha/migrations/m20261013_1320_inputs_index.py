# SPDX-License-Identifier: AGPL-3.0-or-later
"""Search index for the inputs library: plain term tables, not a database-specific full-text engine, so it
works the same on every database Kosha supports.

* ``input_index``      terms from an input's own fields (title, tags, source, links...), per input.
* ``input_text_terms`` terms from the text read out of a file, per file fingerprint, so identical files
  are indexed once and adding a known file to another input needs no new indexing.

Terms are stored lower case; the case-insensitive collation only lets prefix searches (``term LIKE 'pump%'``)
use the index in SQLite.
"""
SQL = """
CREATE TABLE IF NOT EXISTS input_index (
  input_id INTEGER NOT NULL,
  term TEXT NOT NULL COLLATE NOCASE,
  weight REAL NOT NULL,
  PRIMARY KEY (input_id, term)
);
CREATE INDEX IF NOT EXISTS ix_input_index_term ON input_index(term);

CREATE TABLE IF NOT EXISTS input_text_terms (
  sha TEXT NOT NULL,
  term TEXT NOT NULL COLLATE NOCASE,
  weight REAL NOT NULL,
  PRIMARY KEY (sha, term)
);
CREATE INDEX IF NOT EXISTS ix_input_text_terms_term ON input_text_terms(term);
"""
