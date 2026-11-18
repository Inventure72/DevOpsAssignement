"""Real Rooms-only persistence and authorization at the extraction boundary."""

import json

import pytest
from fastapi.testclient import TestClient

from services.rooms.app import create_app
from shared.development import ServiceConfig


def admission(nickname="Host"):
    songs = [
        {
            "song_key": f"demo:{n}",
            "title": f"Song {n}",
            "artist": "Artist",
            "artists": [{"artist_key": "demo:artist", "name": "Artist"}],
            "preview_url": f"/clips/{n}.mp3",
            "familiarity": "easy",
        }
        for n in range(12)
    ]
    return {
        "nickname": nickname,
        "mode": "demo",
        "imported": {"provider": "demo", "evidence": "simulated", "songs": songs},
    }


@pytest.fixture
def config(tmp_path):
    return ServiceConfig(
        f"sqlite:///{tmp_path}/rooms.sqlite3", "test-service", "test-sha"
    )


def test_admission_lobby_privacy_and_host_only_snapshot(config):
    with TestClient(create_app(config)) as client:
        service = {"X-Service-Token": config.service_token}
        assert client.post("/internal/rooms", json=admission()).status_code == 401
        host_response = client.post(
            "/internal/rooms", json=admission(), headers=service
        )
        assert host_response.status_code == 201
        host = host_response.json()
        guest = client.post(
            f"/internal/rooms/{host['room_id']}/join",
            json=admission("Guest"),
            headers=service,
        ).json()
        path = f"/api/rooms/{host['room_id']}/lobby"
        assert client.get(path).status_code == 401
        assert (
            client.get(path, headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )
        auth = {"Authorization": f"Bearer {host['token']}"}
        lobby = client.get(path, headers=auth).json()
        assert len(lobby["players"]) == 2
        assert "session_token_hash" not in json.dumps(lobby)
        assert "listeners" not in json.dumps(lobby)
        assert "songs" not in lobby
        assert client.get(f"/api/room-codes/{host['code']}").status_code == 200
        snapshot_path = f"/internal/rooms/{host['room_id']}/snapshot"
        assert client.get(snapshot_path, headers=auth).status_code == 401
        assert client.get(snapshot_path, headers=service).status_code == 401
        assert (
            client.get(
                snapshot_path,
                headers=service | {"Authorization": f"Bearer {guest['token']}"},
            ).status_code
            == 403
        )
        snapshot = client.get(snapshot_path, headers=service | auth)
        assert snapshot.status_code == 200
        facts = snapshot.json()
        assert facts["host_id"] == host["player_id"]
        assert len(facts["songs"]) == 12
        assert len(facts["songs"][0]["listeners"]) == 2
        assert "session_token_hash" not in snapshot.text
        assert (
            client.post(
                f"/api/rooms/{host['room_id']}/heartbeat",
                headers=auth,
            ).status_code
            == 200
        )


def test_own_schema_restart_and_database_health(config, monkeypatch):
    app = create_app(config)
    with TestClient(app) as client:
        response = client.post(
            "/internal/rooms",
            json=admission(),
            headers={"X-Service-Token": config.service_token},
        )
        code = response.json()["code"]
        assert client.get("/health").json() == {
            "status": "ok",
            "db": "ok",
            "version": "test-sha",
        }
        with app.state.db.read() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert {"rooms", "players", "songs", "player_songs"} <= tables
            assert not {"games", "rounds", "answers"} & tables
            assert conn.execute("SELECT COUNT(*) FROM health_probe").fetchone()[0] == 0
    with TestClient(create_app(config)) as client:
        assert client.get(f"/api/room-codes/{code}").status_code == 200
        with client.app.state.db.read() as conn:
            assert (
                conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
                == 2
            )
    with TestClient(app) as client:

        def unavailable():
            raise OSError("own database offline")

        monkeypatch.setattr(app.state.db, "check", unavailable)
        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["db"] == "unavailable"


def test_disabled_internal_api_and_malformed_import(config):
    with TestClient(create_app(ServiceConfig(config.database_url))) as client:
        assert client.post("/internal/rooms", json=admission()).status_code == 503
    with TestClient(create_app(config)) as client:
        payload = admission()
        payload["imported"]["songs"][0]["artists"] = []
        assert (
            client.post(
                "/internal/rooms",
                json=payload,
                headers={
                    "X-Service-Token": config.service_token,
                },
            ).status_code
            == 422
        )
        with client.app.state.db.read() as conn:
            assert conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0
