# SPDX-License-Identifier: AGPL-3.0-or-later
"""In-app notifications remember the notice they came from, so a notice queued twice shows once under the bell."""
SQL = """
ALTER TABLE notifications ADD COLUMN notice_key TEXT;
CREATE INDEX IF NOT EXISTS ix_notifications_key ON notifications(user_id, notice_key);
"""
