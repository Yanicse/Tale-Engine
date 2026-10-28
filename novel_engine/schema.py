"""SQLite schema version through PRAGMA user_version; explicit, ordered migrations.

Resuming or reading never applies a migration. Existing databases migrate
only through `campaign_cli.py migrate-schema --apply`; a new database is born up to date.
The historical CREATE TABLE IF NOT EXISTS statements of the modules stay valid and unchanged.
"""


def _reference(con):
    """Version 1: record of the existing schema, with no modification."""


def attempt_archives_migration(con):
    """Version 2: bulky context of finished attempts, compressed separately."""
    con.execute('''CREATE TABLE IF NOT EXISTS narrative_attempt_archives (
        request_id TEXT PRIMARY KEY, codec TEXT NOT NULL, payload BLOB NOT NULL)''')


MIGRATIONS = ((1, 'reference', _reference), (2, 'attempt_archives', attempt_archives_migration))
CURRENT = MIGRATIONS[-1][0]


def version(con):
    return con.execute('PRAGMA user_version').fetchone()[0]


def check_supported(con):
    found = version(con)
    if found > CURRENT:
        raise ValueError(f'Campaign schema {found} is newer than this engine ({CURRENT}); the code needs an update.')
    return found


def pending(con):
    found = check_supported(con)
    return [(number, name) for number, name, _ in MIGRATIONS if number > found]


def migrate(con):
    """Apply the missing migrations in the transaction opened by the caller."""
    applied = []
    found = check_supported(con)
    for number, name, step in MIGRATIONS:
        if number > found:
            step(con)
            con.execute(f'PRAGMA user_version={number}')
            applied.append({'version': number, 'name': name})
    return applied
