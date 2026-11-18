"""Idempotent local Rooms database/login bootstrap; credentials stay in env/stdin."""

import os
import re
from pathlib import Path

from services.rooms.storage import connect_sql, connection_options


def initialize(admin_url, password):
    if not password or len(password) > 128 or "\x00" in password:
        raise ValueError("A Rooms database password of 1–128 characters is required")
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
            values = (
                {"rooms_password": password} if "%(rooms_password)s" in batch else None
            )
            cursor.execute(batch, values)
            while cursor.nextset():
                pass
    finally:
        raw.close()


if __name__ == "__main__":
    initialize(os.environ["SQL_ADMIN_URL"], os.environ["ROOMS_DB_PASSWORD"])
    print("Local Rooms database and scoped login initialized")
