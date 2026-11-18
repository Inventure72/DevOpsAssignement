"""Frozen facts and retries over the real independent Game API/database."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from services.game.app import create_app
from shared.development import ServiceConfig


def start_payload():
    players = [
        {
            "id": f"p{i}",
            "player_id": f"p{i}",
            "nickname": f"Player {i}",
            "character_id": "coral",
            "is_host": i == 0,
            "music_status": "demo",
            "last_seen_at_ms": 1000,
        }
        for i in range(3)
    ]
    songs = [
        {
            "song_key": f"song{i}",
            "title": f"Song {i}",
            "artist": "Artist",
            "artists": [{"artist_key": "demo:artist", "name": "Artist"}],
            "preview_url": "/clip",
            "pool_kind": "personal",
            "listeners": [{"player_id": f"p{i % 3}", "familiarity": "easy"}],
        }
        for i in range(60)
    ]
    return {
        "snapshot": {
            "room_id": "room",
            "revision": 3,
            "mode": "demo",
            "host_id": "p0",
            "minimum_players": 3,
            "maximum_players": 10,
            "playtest": False,
            "players": players,
            "songs": songs,
        },
        "settings": {"round_count": 5, "decoys_enabled": False},
        "actor_id": "p0",
        "command": {"request_id": "start", "room_revision": 3},
    }


@pytest.fixture
def config(tmp_path):
    return ServiceConfig(
        f"sqlite:///{tmp_path}/game.sqlite3", "test-service", "test-sha"
    )


def test_frozen_game_retry_authority_and_restart(config):
    payload = start_payload()
    headers = {"X-Service-Token": config.service_token}
    with TestClient(create_app(config, clock=lambda: 1000)) as client:
        assert client.post("/internal/games", json=payload).status_code == 401
        created = client.post("/internal/games", json=payload, headers=headers)
        assert created.status_code == 200
        gid = created.json()["game_id"]
        assert (
            client.post("/internal/games", json=payload, headers=headers).json()
            == created.json()
        )
        conflict = deepcopy(payload)
        conflict["command"]["room_revision"] += 1
        assert (
            client.post("/internal/games", json=conflict, headers=headers).status_code
            == 409
        )
        # Changed source facts cannot rewrite an already accepted game.
        payload["snapshot"]["players"][0]["nickname"] = "Changed later"
        assert (
            client.post("/internal/games", json=payload, headers=headers).json()
            == created.json()
        )
        view = client.get(f"/internal/games/{gid}?actor_id=p1", headers=headers).json()
        assert view["leaderboard"][0]["nickname"] == "Player 0"
        assert "songs_snapshot_json" not in view
        assert (
            client.get(
                f"/internal/games/{gid}?actor_id=outsider", headers=headers
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/internal/games/{gid}/audio?actor_id=p1", headers=headers
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/internal/games/{gid}/end",
                json={
                    "actor_id": "p1",
                    "request_id": "end",
                },
                headers=headers,
            ).status_code
            == 403
        )
        with client.app.state.db.read() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert {
                "games",
                "game_players",
                "rounds",
                "answers",
                "game_commands",
            } <= tables
            assert not {"rooms", "players", "songs", "player_songs"} & tables
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    with TestClient(create_app(config)) as client:
        assert client.get("/health").status_code == 200
        assert (
            client.get(
                f"/internal/games/{gid}?actor_id=p0", headers=headers
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/internal/games/{gid}/end",
                json={
                    "actor_id": "p0",
                    "request_id": "end",
                },
                headers=headers,
            ).status_code
            == 200
        )


def test_preparation_readiness_answer_and_reveal_use_server_time(config):
    now = [1000]
    headers = {"X-Service-Token": config.service_token}
    with TestClient(create_app(config, clock=lambda: now[0])) as client:
        payload = start_payload()
        gid = client.post("/internal/games", json=payload, headers=headers).json()[
            "game_id"
        ]
        path = f"/internal/games/{gid}"
        candidates = client.get(path + "/audio?actor_id=p0", headers=headers).json()[
            "candidates"
        ]
        for candidate in candidates:
            response = client.post(
                path + "/preload",
                json={
                    "actor_id": "p0",
                    "request_id": candidate["candidate_id"],
                    "candidate_id": candidate["candidate_id"],
                    "ok": True,
                },
                headers=headers,
            )
            assert response.status_code == 200
        now[0] = 6000
        presence = {
            "actor_id": "p0",
            "host_connected": True,
            "connected_player_ids": ["p0", "p1", "p2"],
        }
        assert (
            client.post(path + "/advance", json=presence, headers=headers).status_code
            == 200
        )
        view = client.get(path + "?actor_id=p0", headers=headers).json()
        rid = view["round"]["id"]
        for actor in presence["connected_player_ids"]:
            assert (
                client.post(
                    path + f"/rounds/{rid}/ready",
                    json={
                        "actor_id": actor,
                        "readiness_generation": 1,
                    },
                    headers=headers,
                ).status_code
                == 200
            )
        assert (
            client.get(path + "?actor_id=p0", headers=headers).json()["phase"]
            == "ready"
        )
        assert (
            client.post(path + "/advance", json=presence, headers=headers).status_code
            == 200
        )
        now[0] = 9000
        client.post(path + "/advance", json=presence, headers=headers)
        assert (
            client.post(
                path + f"/rounds/{rid}/answers",
                json={
                    "actor_id": "p0",
                    "song_guess": None,
                    "who_player_ids": [],
                    "now": 1,
                },
                headers=headers,
            ).status_code
            == 422
        )
        assert (
            client.post(
                path + f"/rounds/{rid}/answers",
                json={
                    "actor_id": "p0",
                    "song_guess": None,
                    "who_player_ids": [],
                },
                headers=headers,
            ).status_code
            == 200
        )
        now[0] = 29000
        assert (
            client.post(path + "/advance", json=presence, headers=headers).status_code
            == 200
        )
        own = client.get(path + "?actor_id=p0", headers=headers).json()
        other = client.get(path + "?actor_id=p1", headers=headers).json()
        assert own["phase"] == "reveal"
        assert own["round"]["reveal"]["my_answer"]["status"] == "submitted"
        assert other["round"]["reveal"]["my_answer"]["status"] == "missing"


@pytest.mark.parametrize(
    "invalid", ["duplicate_player", "unknown_listener", "missing_host"]
)
def test_invalid_snapshot_is_rejected_before_writing(config, invalid):
    payload = start_payload()
    snapshot = payload["snapshot"]
    if invalid == "duplicate_player":
        snapshot["players"][1] = deepcopy(snapshot["players"][0])
    elif invalid == "unknown_listener":
        snapshot["songs"][0]["listeners"][0]["player_id"] = "outsider"
    else:
        snapshot["host_id"] = "outsider"
    with TestClient(create_app(config)) as client:
        assert (
            client.post(
                "/internal/games",
                json=payload,
                headers={
                    "X-Service-Token": config.service_token,
                },
            ).status_code
            == 422
        )
        with client.app.state.db.read() as conn:
            assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0
