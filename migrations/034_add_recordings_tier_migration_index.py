"""Peewee migrations -- 034_add_recordings_tier_migration_index.py."""


def migrate(migrator, database, fake=False, **kwargs):
    migrator.sql(
        'CREATE INDEX IF NOT EXISTS "recordings_tier_migration" '
        'ON "recordings" ("end_time", "path")'
    )


def rollback(migrator, database, fake=False, **kwargs):
    migrator.sql('DROP INDEX IF EXISTS "recordings_tier_migration"')
