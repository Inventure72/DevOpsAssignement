"""Readiness must detect missing domain schema, not only a working probe table."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from services.game.app import create_app as game_app
from services.rooms.app import create_app as rooms_app
from shared.development import DevelopmentDatabase, ServiceConfig


@pytest.mark.parametrize(
    "factory,table",
    [
        (rooms_app, table)
        for table in (
            "rooms",
            "players",
            "songs",
            "player_songs",
            "demo_catalog",
            "room_start_operations",
        )
    ]
    + [
        (game_app, table)
        for table in (
            "games",
            "game_players",
            "rounds",
            "answers",
            "game_commands",
            "game_round_preparations",
        )
    ],
)
def test_missing_domain_table_fails_health_and_restart(tmp_path, factory, table):
    config = ServiceConfig(f"sqlite:///{tmp_path}/service.sqlite3")
    app = factory(config)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        # Empty schema allows dropping a parent as well as a leaf table. Keep
        # the migration receipt and probe intact to reproduce the false green.
        with app.state.db.transaction() as conn:
            conn.execute(f'DROP TABLE "{table}"')
        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["db"] == "unavailable"
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        with TestClient(factory(config)):
            pass


@pytest.mark.parametrize("directory_exists", [False, True])
def test_missing_migrations_fail_before_creating_database(tmp_path, directory_exists):
    migrations = tmp_path / "migrations"
    if directory_exists:
        migrations.mkdir()
    path = tmp_path / "service.sqlite3"
    db = DevelopmentDatabase(
        f"sqlite:///{path}",
        migrations,
        required_tables=("rooms",),
    )
    with pytest.raises(RuntimeError, match="migration files are missing"):
        db.initialize()
    assert not path.exists()
