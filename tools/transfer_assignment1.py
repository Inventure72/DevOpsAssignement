"""Copy a stable Assignment 1 SQLite backup into initialized service databases."""

import argparse
import hashlib
import json
import os
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

ROOMS = {
    "rooms": "id code mode revision state created_at_ms last_completed_at_ms",
    "players": (
        "id room_id nickname nickname_key character_id music_status music_provider "
        "session_token_hash is_host joined_at_ms last_seen_at_ms music_account_hash "
        "shared_music_account"
    ),
    "songs": (
        "id room_id identity_key isrc title artist artists_json preview_url artwork_url "
        "pool_kind"
    ),
    "player_songs": "room_id player_id song_id familiarity",
}
GAME = {
    "games": (
        "id room_id room_revision start_request_id state_version status phase settings_json "
        "songs_snapshot_json round_plan_json started_at_ms prepared_at_ms phase_ends_at_ms "
        "end_reason ended_at_ms"
    ),
    "game_players": "game_id player_id nickname character_id is_host final_score final_rank",
    "rounds": (
        "id game_id round_number attempt status song_key difficulty picked_player_id "
        "starts_at_ms deadline_at_ms readiness_generation readiness_deadline_at_ms "
        "ready_player_ids_json excluded_player_ids_json revealed_at_ms void_reason"
    ),
    "answers": (
        "game_id round_id player_id status song_guess_json who_mode who_player_ids_json "
        "received_at_ms points"
    ),
    "game_commands": (
        "game_id request_id actor_player_id command_type payload_json result_json "
        "accepted_at_ms"
    ),
    "game_round_preparations": (
        "game_id id round_number attempt song_key difficulty picked_player_id "
        "readiness_generation acknowledgements_json"
    ),
}
ORDERED = frozenset({"games", "game_players"})
SOURCE_VERSION = 6


class TransferError(RuntimeError):
    """A safe transfer diagnostic that does not disclose stored private data."""


def _source_snapshot(path):
    try:
        path = Path(path).resolve(strict=True)
        if not path.is_file():
            raise TransferError("Source must be a SQLite backup file")
        for suffix in ("-wal", "-journal"):
            sidecar = path.with_name(path.name + suffix)
            if sidecar.exists() and sidecar.stat().st_size:
                raise TransferError(
                    "Source must be a stable SQLite backup with no pending journal"
                )
        with closing(
            sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as source:
            source.execute("BEGIN")
            if source.execute("PRAGMA user_version").fetchone()[0] != SOURCE_VERSION:
                raise TransferError(
                    "Source must use Assignment 1 SQLite schema version 6"
                )
            if source.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise TransferError(
                    "Assignment 1 backup contains foreign-key violations"
                )
            tables = {}
            fingerprint = hashlib.sha256(b"assignment1-v6-transfer-v1\n")
            for table, fields in (ROOMS | GAME).items():
                names = ", ".join(fields.split())
                order = "rowid" if table in ORDERED else names
                rows = source.execute(
                    f"SELECT {names} FROM {table} ORDER BY {order}"
                ).fetchall()
                fingerprint.update(table.encode("ascii") + b"\n")
                for values in rows:
                    if any(
                        value is not None
                        and not (
                            type(value) is int
                            or (type(value) is str and "\x00" not in value)
                        )
                        for value in values
                    ):
                        raise TransferError(f"Unsupported SQLite value in {table}")
                    fingerprint.update(
                        json.dumps(values, separators=(",", ":")).encode("ascii")
                        + b"\n"
                    )
                tables[table] = rows
            return fingerprint.hexdigest(), tables
    except (OSError, sqlite3.Error) as error:
        raise TransferError("Could not read the Assignment 1 SQLite backup") from error


def _matches(conn, tables, data):
    for table, fields in tables.items():
        columns = fields.split()
        order = " ORDER BY storage_order" if table in ORDERED else ""
        stored = [
            tuple(row[field] for field in columns)
            for row in conn.execute(f"SELECT {', '.join(columns)} FROM {table}{order}")
        ]
        expected = data[table]
        matches = stored == expected if order else Counter(stored) == Counter(expected)
        if not matches:
            raise TransferError(f"Verification failed for transferred table {table}")


def _target_state(conn, tables, other, digest, data):
    if not conn.execute(
        "SELECT TOP (1) 1 AS applied FROM dbo.schema_migrations"
    ).fetchone():
        raise TransferError("Target schema must be initialized before transfer")
    peer = conn.execute(
        "SELECT OBJECT_ID(?, 'U') AS table_id", (f"dbo.{other}",)
    ).fetchone()
    if peer["table_id"] is not None:
        raise TransferError("Transfer requires separate Rooms and Game databases")
    marker = conn.execute(
        "SELECT OBJECT_ID('dbo.assignment1_transfer', 'U') AS table_id"
    ).fetchone()
    if marker["table_id"] is not None:
        receipt = conn.execute(
            "SELECT fingerprint FROM dbo.assignment1_transfer"
        ).fetchall()
        if [row["fingerprint"] for row in receipt] != [digest]:
            raise TransferError("Target already has a different transfer receipt")
        _matches(conn, tables, data)
        return "already_copied"
    for table in tables:
        if conn.execute(f"SELECT TOP (1) 1 AS present FROM {table}").fetchone():
            raise TransferError(
                f"Target table {table} is not empty; no rows will be overwritten"
            )
    return "empty"


def _copy(conn, tables, data, digest):
    for table, fields in tables.items():
        columns = fields.split()
        if data[table]:
            conn.executemany(
                f"INSERT INTO {table} ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                data[table],
            )
    _matches(conn, tables, data)
    conn.execute(
        "CREATE TABLE dbo.assignment1_transfer ("
        "fingerprint CHAR(64) NOT NULL PRIMARY KEY, "
        "applied_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME())"
    )
    conn.execute(
        "INSERT INTO dbo.assignment1_transfer (fingerprint) VALUES (?)", (digest,)
    )


def transfer(sqlite_path, rooms_db, game_db):
    """Copy once per target, or verify a previous copy before skipping it."""
    digest, data = _source_snapshot(sqlite_path)
    targets = (
        ("rooms", rooms_db, ROOMS, "games"),
        ("game", game_db, GAME, "rooms"),
    )
    result = {
        "fingerprint": digest,
        "counts": {key: len(rows) for key, rows in data.items()},
    }
    for name, db, tables, other in targets:
        try:
            with db.transaction() as conn:
                _target_state(conn, tables, other, digest, data)
        except (*db.errors, TransferError) as error:
            detail = (
                str(error)
                if isinstance(error, TransferError)
                else "SQL preflight failed"
            )
            raise TransferError(f"{name} database: {detail}") from error
    for name, db, tables, other in targets:
        try:
            with db.transaction() as conn:
                state = _target_state(conn, tables, other, digest, data)
                if state == "empty":
                    _copy(conn, tables, data, digest)
            result[name] = "copied" if state == "empty" else state
        except (*db.errors, TransferError) as error:
            detail = (
                str(error)
                if isinstance(error, TransferError)
                else "SQL transfer failed"
            )
            raise TransferError(
                f"{name} database: {detail}. Keep both services offline and retry with the "
                "same backup and target databases. Receipts verify any committed copy."
            ) from error
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, required=True, help="offline SQLite backup"
    )
    parser.add_argument(
        "--apply", action="store_true", help="copy rows into both SQL targets"
    )
    args = parser.parse_args()
    if not args.apply:
        parser.error("--apply is required to copy into both databases")
    if not os.environ.get("ROOMS_DATABASE_URL") or not os.environ.get(
        "GAME_DATABASE_URL"
    ):
        parser.error("ROOMS_DATABASE_URL and GAME_DATABASE_URL must be configured")
    from services.game.storage import GameDatabase
    from services.rooms.storage import RoomsDatabase

    try:
        result = transfer(
            args.source,
            RoomsDatabase(os.environ["ROOMS_DATABASE_URL"]),
            GameDatabase(os.environ["GAME_DATABASE_URL"]),
        )
    except TransferError as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
