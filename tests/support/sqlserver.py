"""Prove SQL acceptance is targeting the disposable test database."""

import secrets

from shared.sqlserver import connection_options


def guarded_database(url, marker, database_factory, owner):
    message = "SQL acceptance requires the disposable database from tools.test_rooms_sqlserver"
    options = connection_options(url)
    if not marker or (options["server"], options["database"], options["user"]) != (
        "127.0.0.1",
        f"repeat_{owner}",
        f"repeat_{owner}_user",
    ):
        raise RuntimeError(message)
    database = database_factory(url)
    try:
        with database.read() as conn:
            row = conn.execute(f"SELECT marker FROM {owner}_test_guard").fetchone()
    except database.errors:
        raise RuntimeError(message) from None
    if not row or not secrets.compare_digest(row["marker"], marker):
        raise RuntimeError(message)
    return database
