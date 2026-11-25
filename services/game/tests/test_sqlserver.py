"""SQL Server acceptance for isolated Game state and Assignment 1 transfer."""

import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from copy import deepcopy

import httpx
import pymssql
import pytest
from fastapi.testclient import TestClient

from backend.storage.database import Database
from services.game.app import create_app
from services.game.domain.repository import SqlServerGameRepository
from services.game.domain.service import GameService
from services.game.domain.views import audio_manifest
from services.game.storage import GameDatabase
from services.game.tests.test_api import start_payload
from services.game.tests.test_api import (
    test_preparation_readiness_answer_and_reveal_use_server_time as play_round,
)
from services.rooms.app import create_app as create_rooms
from services.rooms.domain import repository as rooms_repo
from services.rooms.domain.service import RoomsService
from services.rooms.game_client import GameClient
from services.rooms.storage import RoomsDatabase
from services.rooms.tests.test_api import admission
from shared.development import ServiceConfig
from tests.integration.test_split_browser_api import exercise_split_browser_api
from tests.support.sqlserver import guarded_database
from tools.transfer_assignment1 import TransferError, transfer

GAME_URL = os.environ.get("GAME_TEST_DATABASE_URL")
ROOMS_URL = os.environ.get("ROOMS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not (GAME_URL and ROOMS_URL),
    reason="Run python -m tools.test_rooms_sqlserver for real SQL Server checks",
)


@pytest.fixture
def game_db():
    database = guarded_database(
        GAME_URL, os.environ.get("GAME_TEST_GUARD"), GameDatabase, "game"
    )
    with database.transaction() as conn:
        for room in conn.execute(
            "SELECT DISTINCT room_id,room_id_bytes FROM games"
        ).fetchall():
            SqlServerGameRepository().delete_room_games(conn, room["room_id"])
        conn.execute("DROP TABLE IF EXISTS dbo.assignment1_transfer")
    return database


@pytest.fixture
def rooms_db():
    database = guarded_database(
        ROOMS_URL, os.environ.get("ROOMS_TEST_GUARD"), RoomsDatabase, "rooms"
    )
    with database.transaction() as conn:
        for room in conn.execute("SELECT id FROM rooms").fetchall():
            rooms_repo.delete_room(conn, room["id"])
        conn.execute("DROP TABLE IF EXISTS dbo.assignment1_transfer")
    return database


def test_split_browser_api_with_scoped_logins(rooms_db, game_db):
    exercise_split_browser_api(ROOMS_URL, GAME_URL)


def test_game_timed_round_and_owned_constraints(game_db):
    play_round(ServiceConfig(GAME_URL, "sql-service", "sql-test"))
    repo = SqlServerGameRepository()
    with game_db.read() as conn:
        latest = repo.latest(conn, "room")
        gid = latest["id"]
        assert latest["phase"] == "reveal"
        assert repo.latest(conn, "room ") is None
        assert [p["player_id"] for p in repo.roster(conn, gid)] == ["p0", "p1", "p2"]
        round_id = repo.current(conn, gid)["id"]
        assert len(repo.answers(conn, round_id)) == 3
        assert (
            conn.execute("SELECT OBJECT_ID('dbo.rooms','U') AS id").fetchone()["id"]
            is None
        )
    with pytest.raises(pymssql.DatabaseError):
        with game_db.read() as conn:
            conn.execute("SELECT id FROM repeat_rooms.dbo.rooms").fetchall()
    with pytest.raises(pymssql.IntegrityError):
        with game_db.transaction() as conn:
            conn.execute(
                "UPDATE answers SET player_id='outsider' WHERE round_id=? AND player_id='p0'",
                (round_id,),
            )
    with game_db.transaction() as conn:
        repo.delete_room_games(conn, "room")
        assert repo.game(conn, gid) is None
        assert repo.answers(conn, round_id) == []


def test_game_concurrent_starts_frozen_data_receipts_and_health(game_db):
    config = ServiceConfig(GAME_URL, "sql-service", "sql-test")
    payload = start_payload()
    payload["snapshot"]["players"][0]["nickname"] = "Frozen 🎵\x00text"
    headers = {"X-Service-Token": config.service_token}
    with (
        TestClient(create_app(config, clock=lambda: 1_800_000_000_000)) as first,
        TestClient(create_app(config, clock=lambda: 1_800_000_000_000)) as second,
        ThreadPoolExecutor(max_workers=2) as workers,
    ):

        def start(client):
            response = client.post("/internal/games", headers=headers, json=payload)
            assert response.status_code == 200, response.text
            return response.json()["game_id"]

        first_id, second_id = workers.map(start, (first, second))
        assert first_id == second_id
        payload["snapshot"]["players"][0]["nickname"] = "Later name"
        assert start(second) == first_id
        different_request = deepcopy(payload)
        different_request["command"]["request_id"] += " "
        response = first.post(
            "/internal/games", headers=headers, json=different_request
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "game_active"
        response = first.get(f"/internal/games/{first_id}?actor_id=p0", headers=headers)
        assert response.status_code == 200
        assert response.json()["leaderboard"][0]["nickname"] == "Frozen 🎵\x00text"
        assert first.get("/health").json() == {
            "status": "ok",
            "db": "ok",
            "version": "sql-test",
        }
        with game_db.transaction() as conn:
            conn.execute(
                "EXEC sp_rename 'game_round_preparations','game_preparations_offline'"
            )
        try:
            assert first.get("/health").status_code == 503
        finally:
            with game_db.transaction() as conn:
                conn.execute(
                    "EXEC sp_rename 'game_preparations_offline','game_round_preparations'"
                )
    game_db.initialize()
    with game_db.read() as conn:
        applied = conn.execute("SELECT name FROM schema_migrations ORDER BY name")
        assert [receipt["name"] for receipt in applied] == [
            "001_init.sql",
            "002_game_duration.sql",
        ]
        game = SqlServerGameRepository().game(conn, first_id)
        assert game["started_at_ms"] == 1_800_000_000_000
        assert game["duration_ms"] is None


def legacy_backup(tmp_path):
    legacy = Database(tmp_path / "assignment1.sqlite3")
    legacy.initialize()
    rooms, game = RoomsService(), GameService()
    now = 1_800_000_000_000
    with legacy.transaction() as conn:
        host = rooms.create(
            conn, "Legacy 🎵", "coral", "demo", now, imported=admission()["imported"]
        )
        rid, actor = host["room"]["id"], host["player"]["id"]
        for nickname in ("Middle", "Last"):
            rooms.join(
                conn, rid, nickname, "sage", now, imported=admission()["imported"]
            )
        snapshot = rooms.snapshot(conn, rid)
        settings = {"round_count": 5, "decoys_enabled": False, "answer_seconds": 20}
        old = {"request_id": "legacy-start", "room_revision": snapshot["revision"]}
        finished = game.start(conn, snapshot, settings, actor, old, now)["game_id"]
        revealed_at = reveal_legacy_round(conn, game, finished, snapshot, actor, now)
        game.end(conn, finished, actor, {"request_id": "legacy-end"}, revealed_at)
        active = game.start(
            conn,
            snapshot,
            settings,
            actor,
            {**old, "request_id": "legacy-active"},
            revealed_at,
        )["game_id"]
        reveal_legacy_round(conn, game, active, snapshot, actor, revealed_at)
        rooms.set_state(conn, rid, "playing")
    path = tmp_path / "checkpoint.sqlite3"
    with legacy.read() as source, closing(sqlite3.connect(path)) as backup:
        source.backup(backup)
    return path, host, snapshot, settings, old, finished, active


def reveal_legacy_round(conn, game, game_id, snapshot, actor, now):
    for candidate in audio_manifest(game.repo.game(conn, game_id))["candidates"]:
        candidate_id = candidate["candidate_id"]
        game.preload(
            conn,
            game_id,
            actor,
            {
                "request_id": "preload-" + candidate_id,
                "candidate_id": candidate_id,
                "ok": True,
            },
            now,
        )
    game.advance(conn, game_id, now + 5000)
    attempt = game.repo.current(conn, game_id)
    for player in snapshot["players"]:
        game.ready(conn, game_id, attempt["id"], player["id"], 1, now + 5000)
    attempt = game.repo.current(conn, game_id)
    started = attempt["starts_at_ms"]
    game.advance(conn, game_id, started)
    song = next(
        song for song in snapshot["songs"] if song["song_key"] == attempt["song_key"]
    )
    game.answer(
        conn,
        game_id,
        attempt["id"],
        actor,
        {
            "song_guess": song,
            "who_player_ids": [p["player_id"] for p in song["listeners"]],
        },
        started,
    )
    game.advance(conn, game_id, attempt["deadline_at_ms"])
    return attempt["deadline_at_ms"]


def test_transfer_retries_after_second_target_rolls_back(tmp_path, rooms_db, game_db):
    path, host, snapshot, settings, command, finished, active = legacy_backup(tmp_path)
    unchanged = path.read_bytes()
    repo = SqlServerGameRepository()
    rid, actor = host["room"]["id"], host["player"]["id"]
    with TestClient(create_app(ServiceConfig(GAME_URL, "sql-service"))) as client:
        assert (
            client.post(
                "/internal/games",
                headers={"X-Service-Token": "sql-service"},
                json=start_payload(),
            ).status_code
            == 200
        )
    with pytest.raises(TransferError, match="Target table games is not empty"):
        transfer(path, rooms_db, game_db)
    with rooms_db.read() as conn:
        assert rooms_repo.room(conn, rid) is None
    with game_db.transaction() as conn:
        repo.delete_room_games(conn, "room")

    class FailedSecondCommit:
        errors = game_db.errors
        transactions = 0

        @contextmanager
        def transaction(self):
            self.transactions += 1
            with game_db.transaction() as conn:
                yield conn
                if self.transactions == 2:
                    raise pymssql.OperationalError(
                        "simulated second-target commit failure"
                    )

    with pytest.raises(TransferError, match="Keep both services offline and retry"):
        transfer(path, rooms_db, FailedSecondCommit())
    with game_db.read() as conn:
        assert repo.latest(conn, rid) is None
        assert (
            conn.execute(
                "SELECT OBJECT_ID('dbo.assignment1_transfer','U') AS id"
            ).fetchone()["id"]
            is None
        )
    with rooms_db.read() as conn:
        assert rooms_repo.room(conn, rid)["state"] == "playing"
        assert (
            RoomsService().authenticate(conn, rid, host["token"], 1_800_000_000_000)[
                "id"
            ]
            == actor
        )
        assert (
            conn.execute("SELECT COUNT(*) AS n FROM demo_catalog").fetchone()["n"] == 48
        )
    copied = transfer(path, rooms_db, game_db)
    assert (copied["rooms"], copied["game"]) == ("already_copied", "copied")
    assert copied["counts"]["games"] == 2
    assert copied["counts"]["rounds"] == 2
    assert copied["counts"]["answers"] == 6
    assert copied["counts"]["game_round_preparations"] == 1
    repeated = transfer(path, rooms_db, game_db)
    assert (repeated["rooms"], repeated["game"]) == ("already_copied", "already_copied")
    assert repeated["fingerprint"] == copied["fingerprint"]
    assert path.read_bytes() == unchanged
    with game_db.transaction() as conn:
        assert repo.latest(conn, rid)["id"] == active
        historic = repo.history(conn, rid)[0]
        assert historic["id"] == finished
        leader = historic["leaderboard"][0]
        assert (
            leader["player_id"] == actor and leader["score"] > 0 and leader["rank"] == 1
        )
        assert repo.pending_preparation(conn, active)["game_id"] == active
        assert GameService(repository=repo).start(
            conn, snapshot, settings, actor, command, 1_800_000_001_000
        ) == {"game_id": finished}

    def clock():
        return 1_800_000_100_000

    token = "fixture-internal"
    game_app = create_app(ServiceConfig(GAME_URL, token), clock=clock)
    rooms_app = create_rooms(
        ServiceConfig(ROOMS_URL, token),
        clock=clock,
        game_client=GameClient(
            "https://game.test", token, transport=httpx.ASGITransport(app=game_app)
        ),
        lifecycle_interval=None,
    )
    with TestClient(game_app), TestClient(rooms_app) as room_client:
        auth = {"Authorization": f"Bearer {host['token']}"}
        result = room_client.post(f"/api/rooms/{rid}/game/sync", headers=auth)
        assert result.status_code == 200, result.text
        assert result.json()["game"] == {
            "game_id": active,
            "status": "aborted",
            "ended_at_ms": clock(),
            "end_reason": "server_restart",
        }
        assert (
            room_client.get(f"/api/rooms/{rid}/lobby", headers=auth).json()["state"]
            == "lobby"
        )
    with rooms_db.read() as conn:
        assert rooms_repo.room(conn, rid)["last_completed_at_ms"] is None
    with game_db.read() as conn:
        assert repo.history(conn, rid)[1] == historic
        for game_id in (finished, active):
            saved = repo.game(conn, game_id)
            assert saved["duration_ms"] == saved["ended_at_ms"] - saved["started_at_ms"]
