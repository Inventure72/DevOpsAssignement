"""Actual SQL Server acceptance, using a Rooms-only login and disposable data."""

import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pymssql
import pytest
from fastapi.testclient import TestClient

from services.rooms.app import create_app
from services.rooms.domain import demo, music, repository
from services.rooms.domain.service import RETENTION_MS, RoomsService
from services.rooms.storage import RoomsDatabase
from services.rooms.tests.test_api import admission
from shared.development import ServiceConfig
from shared.errors import DomainError
from shared.sqlserver import connect_sql, connection_options
from tests.support.sqlserver import guarded_database

URL = os.environ.get("ROOMS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not URL,
    reason="Run python -m tools.test_rooms_sqlserver for real SQL Server checks",
)


@pytest.fixture
def db():
    database = guarded_database(
        URL, os.environ.get("ROOMS_TEST_GUARD"), RoomsDatabase, "rooms"
    )
    database.initialize()
    with database.transaction() as conn:
        for room in conn.execute("SELECT id FROM rooms").fetchall():
            repository.delete_room(conn, room["id"])
    return database


def test_sql_acceptance_rejects_unmarked_database():
    with pytest.raises(RuntimeError, match="requires the disposable database"):
        guarded_database(URL, secrets.token_hex(32), RoomsDatabase, "rooms")


def test_encrypted_connection_rejects_untrusted_server_certificate():
    options = connection_options(URL)
    options["encryption"] = "require"
    with pytest.raises(pymssql.OperationalError):
        with connect_sql(options):
            pytest.fail("Accepted the disposable SQL Server's untrusted certificate")


def create_room(db, name="Host", now=1_800_000_000_000):
    payload = admission(name)
    with db.transaction() as conn:
        return RoomsService().create(
            conn, name, "coral", "demo", now, imported=payload["imported"]
        )


def test_migrations_seed_repeat_and_scoped_login(db):
    host = create_room(db)
    with db.read() as conn:
        first = conn.execute(
            "SELECT name,sha256,applied_at FROM schema_migrations ORDER BY name"
        ).fetchall()
        catalog = conn.execute(
            "SELECT id,title,artists_json FROM demo_catalog ORDER BY id"
        ).fetchall()
        assert len(first) == 1 and len(catalog) == 100
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM sys.tables WHERE name IN ('games','rounds','answers')"
            ).fetchone()["n"]
            == 0
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: db.initialize(), range(2)))
    with db.read() as conn:
        assert (
            conn.execute(
                "SELECT name,sha256,applied_at FROM schema_migrations ORDER BY name"
            ).fetchall()
            == first
        )
        assert (
            conn.execute(
                "SELECT id,title,artists_json FROM demo_catalog ORDER BY id"
            ).fetchall()
            == catalog
        )
        assert (
            repository.room(conn, host["room"]["id"])["created_at_ms"]
            == 1_800_000_000_000
        )
        assert demo.preview_sample(conn)["title"]
    with pytest.raises(pymssql.DatabaseError):
        with db.read() as conn:
            conn.execute("SELECT id FROM repeat_game.dbo.games").fetchall()


def test_rooms_api_unicode_privacy_restart_and_health(db):
    config = ServiceConfig(URL, "fixture-service", "sql-test")
    headers = {"X-Service-Token": config.service_token}
    with TestClient(create_app(config)) as client:
        assert client.get("/health").json() == {
            "status": "ok",
            "db": "ok",
            "version": "sql-test",
        }
        created = client.post(
            "/internal/rooms", json=admission("🎵" * 24), headers=headers
        )
        assert created.status_code == 201, created.text
        host = created.json()
        auth = {"Authorization": f"Bearer {host['token']}"}
        path = f"/internal/rooms/{host['room_id']}/join"
        assert (
            client.post(path, json=admission("Straße"), headers=headers).status_code
            == 201
        )
        assert (
            client.post(path, json=admission("STRASSE"), headers=headers).status_code
            == 409
        )
        malformed = admission("Invalid music")
        malformed["imported"]["songs"][0]["title"] += "\x00"
        result = client.post(path, json=malformed, headers=headers)
        assert result.status_code == 400
        assert result.json()["error"]["code"] == "invalid_music_import"
        for name in ("Café", "Cafe\u0301"):
            assert (
                client.post(path, json=admission(name), headers=headers).status_code
                == 201
            )
        lobby = client.get(f"/api/rooms/{host['room_id']}/lobby", headers=auth)
        assert lobby.json()["players"][0]["nickname"] == "🎵" * 24
        assert "session_token_hash" not in lobby.text and "listeners" not in lobby.text
        snapshot = client.get(
            f"/internal/rooms/{host['room_id']}/snapshot", headers=headers | auth
        )
        assert snapshot.status_code == 200
        assert len(snapshot.json()["songs"][0]["listeners"]) == 4
    with TestClient(create_app(config)) as client:
        assert client.get(f"/api/room-codes/{host['code']}").status_code == 200
        with client.app.state.db.transaction() as conn:
            conn.execute("EXEC sp_rename 'health_probe', 'health_probe_offline'")
        try:
            assert client.get("/health").status_code == 503
        finally:
            with client.app.state.db.transaction() as conn:
                conn.execute("EXEC sp_rename 'health_probe_offline', 'health_probe'")
        with client.app.state.db.transaction() as conn:
            conn.execute("EXEC sp_rename 'demo_catalog', 'demo_catalog_offline'")
        try:
            assert client.get("/health").status_code == 503
            with pytest.raises(pymssql.DatabaseError):
                RoomsDatabase(URL).initialize()
        finally:
            with client.app.state.db.transaction() as conn:
                conn.execute("EXEC sp_rename 'demo_catalog_offline', 'demo_catalog'")


def test_independent_connections_cannot_overfill_the_last_room_slot(db):
    host = create_room(db)
    rid = host["room"]["id"]
    rooms = RoomsService()
    for n in range(8):
        with db.transaction() as conn:
            rooms.join(
                conn,
                rid,
                f"Guest {n}",
                "sage",
                1_800_000_000_001,
                imported=admission()["imported"],
            )

    def join(name):
        try:
            with db.transaction() as conn:
                rooms.join(
                    conn,
                    rid,
                    name,
                    "sage",
                    1_800_000_000_002,
                    imported=admission()["imported"],
                )
            return "joined"
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(join, ["Last A", "Last B"])) == ["joined", "room_full"]
    with db.read() as conn:
        assert len(repository.players(conn, rid)) == 10


def test_lobby_read_waits_for_join_and_health_does_not_wait_for_write_gate(db):
    host = create_room(db)
    rid = host["room"]["id"]
    rooms = RoomsService()
    reading = Event()

    def lobby():
        reading.set()
        with db.read() as conn:
            return rooms.lobby(conn, rid, 1_800_000_000_002)

    with ThreadPoolExecutor(max_workers=1) as worker:
        with db.transaction() as conn:
            db.check()
            rooms.join(
                conn,
                rid,
                "Guest",
                "sage",
                1_800_000_000_001,
                imported=admission()["imported"],
            )
            pending = worker.submit(lobby)
            assert reading.wait(timeout=2) and not pending.done()
        assert len(pending.result(timeout=5)["players"]) == 2


def test_constraints_membership_deletion_expiry_and_rollback(db):
    host = create_room(db)
    other = create_room(db, "Other")
    rooms = RoomsService()
    rid = host["room"]["id"]
    with db.transaction() as conn:
        guest = rooms.join(
            conn,
            rid,
            "Guest",
            "sage",
            1_800_000_000_001,
            imported=admission("Guest")["imported"],
        )
        song = repository.songs(conn, rid)[0]
        original_count = repository.song_counts(conn, rid)[guest["player"]["id"]]
        music.store(conn, rid, guest["player"]["id"], admission()["imported"]["songs"])
        assert (
            repository.song_counts(conn, rid)[guest["player"]["id"]] == original_count
        )
        assert rooms.expired_ids(conn, 1_800_000_000_000 + RETENTION_MS - 1) == []
        assert rid in rooms.expired_ids(conn, 1_800_000_000_000 + RETENTION_MS)
    with pytest.raises(pymssql.IntegrityError):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO player_songs VALUES (?,?,?,?)",
                (other["room"]["id"], guest["player"]["id"], song["id"], "easy"),
            )
    with pytest.raises(pymssql.IntegrityError):
        with db.transaction() as conn:
            conn.execute(
                "UPDATE players SET is_host=2 WHERE id=?", (guest["player"]["id"],)
            )
    with pytest.raises(pymssql.IntegrityError):
        with db.transaction() as conn:
            conn.execute("UPDATE rooms SET mode='demo ' WHERE id=?", (rid,))
    with pytest.raises(pymssql.IntegrityError):
        with db.transaction() as conn:
            conn.execute(
                "UPDATE players SET is_host=1 WHERE id=?", (guest["player"]["id"],)
            )
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            rooms.join(
                conn,
                rid,
                "Rolled back",
                "sage",
                1_800_000_000_002,
                imported=admission()["imported"],
            )
            raise RuntimeError("forced late admission failure")
    with db.transaction() as conn:
        assert len(repository.players(conn, rid)) == 2
        assert repository.room(conn, rid + " ") is None
        rooms.remove_player(conn, rid, guest["player"]["id"], 1_800_000_000_003)
        assert len(repository.songs(conn, rid)) == 12
        rooms.delete(conn, rid)
        assert repository.room(conn, rid) is None
        assert repository.room(conn, other["room"]["id"])
        assert (
            conn.execute("SELECT COUNT(*) AS n FROM demo_catalog").fetchone()["n"]
            == 100
        )


def test_exact_recording_keys_json_and_both_membership_boundaries(db):
    host = create_room(db)
    other = create_room(db, "Other")
    rid = host["room"]["id"]
    actor = host["player"]["id"]
    with db.transaction() as conn:
        songs = admission()["imported"]["songs"]
        variants = [
            dict(songs[0], song_key=songs[0]["song_key"] + " "),
            dict(songs[1], isrc="Z" * 1000),
        ]
        music.store(conn, rid, actor, variants)
        assert len(repository.songs(conn, rid)) == 14
        assert repository.song_counts(conn, rid)[actor] == 14
        music.store(conn, rid, actor, variants)
        assert repository.song_counts(conn, rid)[actor] == 14
        first_song = repository.songs(conn, rid)[0]["id"]
        other_song = repository.songs(conn, other["room"]["id"])[0]["id"]
    for player, song in [(actor, other_song), (other["player"]["id"], first_song)]:
        with pytest.raises(pymssql.IntegrityError):
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO player_songs VALUES (?,?,?,?)",
                    (rid, player, song, "easy"),
                )
    for invalid in ("[]", "{}", "broken JSON"):
        with pytest.raises(pymssql.IntegrityError):
            with db.transaction() as conn:
                conn.execute(
                    "UPDATE songs SET artists_json=? WHERE id=?", (invalid, first_song)
                )


def test_seed_validation_rollback_and_room_copies(db, tmp_path):
    host = create_room(db)
    with db.read() as conn:
        before = conn.execute("SELECT * FROM demo_catalog ORDER BY id").fetchall()
    entries = json.loads(db.seed.read_text())
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps(entries[:2]))
    with db.transaction() as conn:
        demo.seed_demo(conn, seed)
        assert (
            conn.execute("SELECT COUNT(*) AS n FROM demo_catalog").fetchone()["n"] == 2
        )
        assert len(repository.songs(conn, host["room"]["id"])) == 12
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            demo.seed_demo(conn, db.seed)
            raise RuntimeError("forced seed rollback")
    with db.read() as conn:
        assert (
            conn.execute("SELECT COUNT(*) AS n FROM demo_catalog").fetchone()["n"] == 2
        )
    entries[0]["artists"] = []
    seed.write_text(json.dumps(entries))
    with pytest.raises(ValueError):
        with db.transaction() as conn:
            demo.seed_demo(conn, seed)
    db.initialize()
    with db.read() as conn:
        assert (
            conn.execute("SELECT * FROM demo_catalog ORDER BY id").fetchall() == before
        )


def test_failed_migration_rolls_back_and_applied_files_are_immutable(db, tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    initial = db.migrations / "001_init.sql"
    (migrations / initial.name).write_bytes(initial.read_bytes())
    bad = migrations / "002_failure.sql"
    bad.write_text(
        "CREATE TABLE rollback_probe(id INT); THROW 51000, 'fixture failure', 1;"
    )
    candidate = RoomsDatabase(URL, migrations=migrations)
    with pytest.raises(pymssql.DatabaseError):
        candidate.initialize()
    with db.read() as conn:
        assert (
            conn.execute("SELECT OBJECT_ID('rollback_probe','U') AS id").fetchone()[
                "id"
            ]
            is None
        )
        assert (
            conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
            == 1
        )
    bad.unlink()
    early = migrations / "000_early.sql"
    early.write_text("CREATE TABLE early_probe(id INT);")
    with pytest.raises(RuntimeError, match="ordered file prefix"):
        candidate.initialize()
    with db.read() as conn:
        assert (
            conn.execute("SELECT OBJECT_ID('early_probe','U') AS id").fetchone()["id"]
            is None
        )
    early.unlink()
    (migrations / initial.name).write_text(
        initial.read_text() + "\n-- changed applied file"
    )
    with pytest.raises(RuntimeError, match="migration changed"):
        candidate.initialize()
