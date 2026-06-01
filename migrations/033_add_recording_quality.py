"""Peewee migrations -- 033_add_recording_quality.py.

Add quality column to recordings table for dual-stream playback support.
Values: "full" (default) or "mobile" (playback stream).
"""

import peewee as pw

SQL = pw.SQL


def migrate(migrator, database, fake=False, **kwargs):
    migrator.sql(
        'ALTER TABLE recordings ADD COLUMN quality VARCHAR(10) NOT NULL DEFAULT "full"'
    )
    migrator.sql(
        'CREATE INDEX IF NOT EXISTS "recordings_quality" ON "recordings" ("quality")'
    )


def rollback(migrator, database, fake=False, **kwargs):
    migrator.sql("DROP INDEX IF EXISTS recordings_quality")
    migrator.sql("ALTER TABLE recordings DROP COLUMN quality")
