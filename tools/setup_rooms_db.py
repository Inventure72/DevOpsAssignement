"""Idempotent local service database and login bootstrap."""

import os
import re
from pathlib import Path

from shared.sqlserver import connect_sql, connection_options


def initialize(admin_url, rooms_password, game_password):
    passwords = {"rooms_password": rooms_password, "game_password": game_password}
    if (
        any(
            not password
            or len(password.encode("utf-16-le")) > 256
            or "\x00" in password
            for password in passwords.values()
        )
        or rooms_password == game_password
    ):
        raise ValueError(
            "Provide two distinct SQL Server passwords of 1–128 UTF-16 units"
        )
    options = connection_options(admin_url)
    options["autocommit"] = True
    raw = connect_sql(options)
    try:
        cursor = raw.cursor()
        sql = (Path(__file__).resolve().parents[1] / "db/init.sql").read_text()
        # USE is compiled before CREATE DATABASE runs within a single batch.
        # Submit explicit GO-delimited bootstrap batches in order.
        for batch in re.split(r"(?im)^GO\s*$", sql):
            if not batch.strip():
                continue
            values = {
                key: value for key, value in passwords.items() if f"%({key})s" in batch
            }
            cursor.execute(batch, values or None)
            while cursor.nextset():
                pass
    finally:
        raw.close()


if __name__ == "__main__":
    initialize(
        os.environ["SQL_ADMIN_URL"],
        os.environ["ROOMS_DB_PASSWORD"],
        os.environ["GAME_DB_PASSWORD"],
    )
    print("Local Rooms and Game databases and scoped logins initialized")
