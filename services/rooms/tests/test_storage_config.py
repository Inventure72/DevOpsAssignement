"""Fail-closed SQL connection configuration without database or secret output."""

import pymssql
import pytest

from services.rooms.storage import RoomsDatabase
from shared.development import ServiceConfig
from shared.sqlserver import connection_options


def test_sql_url_defaults_to_encryption_and_decodes_credentials():
    options = connection_options(
        "mssql+pymssql://rooms:p%40ss%27word@sql.example/rooms"
    )
    assert options["password"] == "p@ss'word"
    assert options["encryption"] == "require"
    assert options["database"] == "rooms"
    assert options["timeout"] == 5 and options["login_timeout"] == 5
    assert options["as_dict"] is True
    config = ServiceConfig(
        "mssql+pymssql://rooms:private-password@host/rooms", "private-token"
    )
    assert "private-password" not in repr(config) and "private-token" not in repr(
        config
    )


@pytest.mark.parametrize(
    "url",
    [
        "",
        "sqlite:///rooms.sqlite3",
        "mssql+pymssql://rooms@host/rooms",
        "mssql+pymssql://rooms:private-password@host/rooms?encryption=off",
        "mssql+pymssql://rooms:private-password@host/rooms?ignored=true",
        "mssql+pymssql://rooms:private-password@host:bad/rooms",
    ],
)
def test_invalid_or_unencrypted_remote_configuration_fails_safely(url):
    with pytest.raises(ValueError) as error:
        connection_options(url)
    assert "private-password" not in str(error.value)


def test_sql_missing_migrations_fail_without_connecting(tmp_path):
    db = RoomsDatabase("unused", migrations=tmp_path)
    with pytest.raises(RuntimeError, match="migration files are missing"):
        db.initialize()


def test_disconnect_on_rollback_does_not_mask_probe_error(monkeypatch):
    class Disconnected:
        def cursor(self):
            raise pymssql.OperationalError("connection lost during probe")

        def rollback(self):
            raise pymssql._mssql.MSSQLDriverException("cannot roll back")

        def close(self):
            raise pymssql._mssql.MSSQLDriverException("already closed")

    db = RoomsDatabase("unused")
    monkeypatch.setattr(db, "_connect", Disconnected)
    with pytest.raises(pymssql.OperationalError, match="lost during probe"):
        db.check()
