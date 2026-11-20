"""Source-side checks using a real Assignment 1 SQLite backup."""

import sqlite3
from contextlib import closing

import pytest

from backend.storage.database import Database
from tools.transfer_assignment1 import TransferError, transfer


def test_transfer_reads_backup_without_modifying_it(tmp_path):
    legacy = Database(tmp_path / "legacy.sqlite3")
    legacy.initialize()
    path = tmp_path / "copy with spaces?.sqlite3"

    class UnavailableTarget:
        errors = (OSError,)

        def transaction(self):
            raise OSError("connection refused")

    with legacy.read() as source:
        source.execute(
            "INSERT INTO rooms (id,code,mode,created_at_ms) VALUES (?,?,?,?)",
            ("0" * 32, "MP4HKQ", "demo", 1_800_000_000_000),
        )
        with closing(sqlite3.connect(path)) as backup:
            source.backup(backup)
        snapshot = path.read_bytes()
        files = set(tmp_path.iterdir())
        with pytest.raises(TransferError, match="rooms database: SQL preflight failed"):
            transfer(path, UnavailableTarget(), UnavailableTarget())
        assert path.read_bytes() == snapshot
        assert set(tmp_path.iterdir()) == files
        with pytest.raises(TransferError, match="no pending journal"):
            transfer(legacy.path, UnavailableTarget(), UnavailableTarget())
        assert source.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 1
