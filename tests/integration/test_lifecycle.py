"""Lifecycle recovery across real Rooms and Game apps with isolated databases."""

from contextlib import ExitStack

import httpx
import pytest
from fastapi.testclient import TestClient

from services.game.app import create_app as create_game
from services.rooms.app import create_app as create_rooms
from services.rooms.game_client import GameClient
from services.rooms.tests.test_api import admission
from shared.development import ServiceConfig
from tests.integration.test_rooms_game_start import auth, lobby, start_request

SERVICE_TOKEN = "lifecycle-integration-service"
INTERNAL = {"X-Service-Token": SERVICE_TOKEN}
START_TIME = 1_800_000_000_000
GAME_URL = "https://game.test"


class Lifecycle:
    def __init__(self, stack, tmp_path):
        self.stack = stack
        self.rooms_config = ServiceConfig(
            f"sqlite:///{tmp_path}/rooms.sqlite3", SERVICE_TOKEN
        )
        self.game_config = ServiceConfig(
            f"sqlite:///{tmp_path}/game.sqlite3", SERVICE_TOKEN
        )
        self.now = START_TIME
        self.offline = False
        self.drop_next_reply = False
        self.dropped = []
        self.forwarded = []
        self.game_context = self.stack.enter_context(ExitStack())
        self.rooms_context = self.stack.enter_context(ExitStack())
        self.restart_game()
        self.restart_rooms()

    def clock(self):
        return self.now

    async def forward(self, request):
        assert request.headers["X-Service-Token"] == SERVICE_TOKEN
        assert "Authorization" not in request.headers
        self.forwarded.append(request.url.path)
        if self.offline:
            raise httpx.ConnectError("Game process is temporarily unreachable")
        response = await self.transport.handle_async_request(request)
        await response.aread()
        if self.drop_next_reply:
            assert response.status_code == 200, response.text
            self.drop_next_reply = False
            self.dropped.append((request.url.path, response.json()))
            raise httpx.ReadTimeout("The Game transaction finished before disconnect")
        return response

    def restart_game(self):
        self.game_context.close()
        app = create_game(self.game_config, clock=self.clock)
        self.transport = httpx.ASGITransport(app=app)
        self.game = self.game_context.enter_context(TestClient(app))

    def restart_rooms(self):
        self.rooms_context.close()
        app = create_rooms(
            self.rooms_config,
            clock=self.clock,
            game_client=GameClient(
                GAME_URL, SERVICE_TOKEN, transport=httpx.MockTransport(self.forward)
            ),
            lifecycle_interval=None,
        )
        self.rooms = self.rooms_context.enter_context(TestClient(app))

    def add_room(self):
        response = self.rooms.post(
            "/internal/rooms", json=admission(), headers=INTERNAL
        )
        assert response.status_code == 201, response.text
        self.host = response.json()
        self.room_id = self.host["room_id"]
        self.room_path = f"/api/rooms/{self.room_id}"
        self.guests = [self.join("First guest"), self.join("Second guest")]

    def join(self, nickname, status=201):
        response = self.rooms.post(
            f"/internal/rooms/{self.room_id}/join",
            json=admission(nickname),
            headers=INTERNAL,
        )
        assert response.status_code == status, response.text
        return response.json()

    def lobby(self):
        return lobby(self.rooms, self.host)

    def start(self, request_id="start", status=200):
        response = self.rooms.post(
            self.room_path + "/start",
            headers=auth(self.host),
            json=start_request(self.rooms, self.host, request_id),
        )
        assert response.status_code == status, response.text
        return response.json()

    def sync(self, player=None, status=200):
        response = self.rooms.post(
            self.room_path + "/game/sync", headers=auth(player or self.host)
        )
        assert response.status_code == status, response.text
        return response.json()

    def heartbeat(self, player=None):
        response = self.rooms.post(
            self.room_path + "/heartbeat", headers=auth(player or self.host)
        )
        assert response.status_code == 200, response.text

    def leave(self, player, request_id, game_id=None, status=200):
        response = self.rooms.post(
            self.room_path + "/leave",
            json={"request_id": request_id, "game_id": game_id},
            headers=auth(player),
        )
        assert response.status_code == status, response.text
        return response.json()

    def game_view(self, game_id):
        response = self.game.get(
            f"/internal/games/{game_id}",
            params={"actor_id": self.host["player_id"]},
            headers=INTERNAL,
        )
        assert response.status_code == 200, response.text
        return response.json()

    def preload(self, game_id):
        game_path = f"/internal/games/{game_id}"
        response = self.game.get(
            game_path + "/audio",
            params={"actor_id": self.host["player_id"]},
            headers=INTERNAL,
        )
        assert response.status_code == 200, response.text
        for candidate in response.json()["candidates"]:
            candidate_id = candidate["candidate_id"]
            response = self.game.post(
                game_path + "/preload",
                json={
                    "actor_id": self.host["player_id"],
                    "request_id": f"preload-{candidate_id}",
                    "candidate_id": candidate_id,
                    "ok": True,
                },
                headers=INTERNAL,
            )
            assert response.status_code == 200, response.text

    def ready_round(self, game_id):
        attempt = self.game_view(game_id)["round"]
        for player in [self.host, *self.guests]:
            response = self.game.post(
                f"/internal/games/{game_id}/rounds/{attempt['id']}/ready",
                json={
                    "actor_id": player["player_id"],
                    "readiness_generation": attempt["readiness_generation"],
                },
                headers=INTERNAL,
            )
            assert response.status_code == 200, response.text
        assert self.game_view(game_id)["phase"] == "ready"
        self.sync()
        return self.game_view(game_id)["round"]


@pytest.fixture
def lifecycle(tmp_path):
    with ExitStack() as stack:
        running = Lifecycle(stack, tmp_path)
        running.add_room()
        yield running


def test_sync_recovers_ambiguous_start_after_peer_outage(lifecycle):
    lifecycle.drop_next_reply = True
    pending = lifecycle.start(status=503)
    assert pending["error"]["code"] == "game_start_pending"
    [(path, committed)] = lifecycle.dropped
    assert path == "/internal/games"
    game_id = committed["game_id"]

    lifecycle.restart_rooms()
    lifecycle.offline = True
    peer_calls = len(lifecycle.forwarded)
    unauthorized = lifecycle.rooms.post(lifecycle.room_path + "/game/sync")
    assert unauthorized.status_code == 401
    assert len(lifecycle.forwarded) == peer_calls
    unavailable = lifecycle.sync(lifecycle.guests[0], status=503)
    assert unavailable["error"]["code"] == "game_lifecycle_unavailable"
    assert lifecycle.rooms.get("/health").status_code == 200
    assert lifecycle.lobby()["state"] == "playing"
    lifecycle.join("Locked guest", status=409)

    lifecycle.offline = False
    recovered = lifecycle.sync(lifecycle.guests[0])["game"]
    assert recovered["game_id"] == game_id
    assert recovered["status"] == "preparing"
    assert "songs_snapshot_json" not in recovered and "listeners" not in recovered
    with lifecycle.game.app.state.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1


def test_leave_retry_after_lost_reply_cannot_reopen_a_new_game(lifecycle):
    guest = lifecycle.guests[0]
    game_id = lifecycle.start()["game_id"]
    unscoped = lifecycle.leave(guest, "unscoped-guest-leave", status=409)
    assert unscoped["error"]["code"] == "game_identity_required"
    assert lifecycle.leave(guest, "guest-active-leave", game_id) == {"accepted": True}
    assert lifecycle.sync(guest)["game"]["game_id"] == game_id
    assert guest["player_id"] in [p["id"] for p in lifecycle.lobby()["players"]]

    lifecycle.now += 1_000
    lifecycle.drop_next_reply = True
    unavailable = lifecycle.leave(lifecycle.host, "leave-old", game_id, status=503)
    assert unavailable["error"]["code"] == "game_lifecycle_unavailable"
    assert lifecycle.game_view(game_id)["end_reason"] == "host_left"
    assert lifecycle.lobby()["state"] == "playing"
    lifecycle.join("Locked guest", status=409)

    lifecycle.restart_rooms()
    assert lifecycle.leave(lifecycle.host, "leave-old", game_id) == {"accepted": True}
    assert lifecycle.lobby()["state"] == "lobby"
    assert lifecycle.lobby()["expires_at_ms"] == START_TIME + 30 * 86_400_000
    assert lifecycle.sync(guest) == {"game": None}
    assert lifecycle.leave(guest, "guest-active-leave", game_id) == {"accepted": True}
    assert lifecycle.sync(guest) == {"game": None}
    assert lifecycle.leave(guest, "guest-lobby-leave") == {"accepted": True}
    assert (
        lifecycle.rooms.post(
            lifecycle.room_path + "/game/sync", headers=auth(guest)
        ).status_code
        == 401
    )
    assert guest["player_id"] in [
        player["player_id"] for player in lifecycle.game_view(game_id)["leaderboard"]
    ]

    lifecycle.join("Replacement guest")
    new_game_id = lifecycle.start("start-new")["game_id"]
    assert new_game_id != game_id
    assert lifecycle.leave(lifecycle.host, "leave-old", game_id) == {"accepted": True}
    assert lifecycle.lobby()["state"] == "playing"
    assert lifecycle.sync()["game"]["game_id"] == new_game_id
    assert lifecycle.game_view(new_game_id)["status"] == "preparing"


def test_exact_host_grace_timeout_uses_live_rooms_heartbeat(lifecycle):
    game_id = lifecycle.start()["game_id"]
    lifecycle.preload(game_id)
    lifecycle.now = START_TIME + 15_000
    assert lifecycle.sync(lifecycle.guests[0])["game"]["status"] == "preparing"
    assert lifecycle.game_view(game_id)["phase"] == "setup"
    lifecycle.heartbeat()
    lifecycle.sync(lifecycle.guests[0])
    assert lifecycle.game_view(game_id)["phase"] == "ready"
    attempt = lifecycle.ready_round(game_id)
    lifecycle.now = attempt["deadline_at_ms"]
    lifecycle.sync(lifecycle.guests[0])

    grace_boundary = START_TIME + 75_000
    lifecycle.now = grace_boundary - 1
    assert lifecycle.sync(lifecycle.guests[0])["game"]["status"] == "playing"
    assert lifecycle.lobby()["state"] == "playing"
    lifecycle.now = grace_boundary
    expired = lifecycle.sync(lifecycle.guests[0])["game"]
    assert expired == {
        "game_id": game_id,
        "status": "aborted",
        "end_reason": "host_timeout",
        "ended_at_ms": grace_boundary,
    }
    assert lifecycle.lobby()["state"] == "lobby"
    assert lifecycle.lobby()["expires_at_ms"] == START_TIME + 30 * 86_400_000
    lifecycle.heartbeat()


def test_game_restart_reconciles_without_aborting_on_rooms_restart(lifecycle):
    game_id = lifecycle.start()["game_id"]
    lifecycle.restart_rooms()
    assert lifecycle.sync()["game"]["status"] == "preparing"
    lifecycle.preload(game_id)
    lifecycle.now += 5_000
    lifecycle.sync()
    attempt = lifecycle.ready_round(game_id)
    lifecycle.now = attempt["starts_at_ms"]
    lifecycle.sync()
    answer = lifecycle.game.post(
        f"/internal/games/{game_id}/rounds/{attempt['id']}/answers",
        json={
            "actor_id": lifecycle.host["player_id"],
            "song_guess": None,
            "who_player_ids": [
                player["player_id"] for player in [lifecycle.host, *lifecycle.guests]
            ],
        },
        headers=INTERNAL,
    )
    assert answer.status_code == 200, answer.text
    lifecycle.now = attempt["deadline_at_ms"]
    lifecycle.sync()
    leaderboard = lifecycle.game_view(game_id)["leaderboard"]
    assert leaderboard[0]["score"] > 0
    lifecycle.now += 1_000
    lifecycle.restart_game()
    assert lifecycle.rooms.get("/health").status_code == 200
    assert lifecycle.lobby()["state"] == "playing"
    terminal = lifecycle.sync()["game"]
    assert terminal["game_id"] == game_id
    assert terminal["status"] == "aborted"
    assert terminal["end_reason"] == "server_restart"
    assert lifecycle.game_view(game_id)["leaderboard"] == leaderboard
    assert lifecycle.lobby()["state"] == "lobby"
    assert lifecycle.lobby()["expires_at_ms"] == START_TIME + 30 * 86_400_000
    assert lifecycle.sync() == {"game": None}


def test_completed_match_renews_retention_once(lifecycle):
    game_id = lifecycle.start()["game_id"]
    lifecycle.preload(game_id)
    lifecycle.now += 5_000
    lifecycle.sync()
    for round_number in range(1, 6):
        attempt = lifecycle.ready_round(game_id)
        assert attempt["round_number"] == round_number
        deadline = attempt["deadline_at_ms"]
        while lifecycle.now < deadline:
            lifecycle.now = min(deadline, lifecycle.now + 10_000)
            lifecycle.heartbeat()
            lifecycle.sync()
        assert lifecycle.game_view(game_id)["phase"] == "reveal"
        lifecycle.now += 10_000
        lifecycle.heartbeat()
        if round_number < 5:
            lifecycle.sync()

    completion_time = lifecycle.now
    lifecycle.drop_next_reply = True
    lost = lifecycle.sync(status=503)
    assert lost["error"]["code"] == "game_lifecycle_unavailable"
    assert lifecycle.game_view(game_id)["status"] == "completed"
    assert lifecycle.lobby()["state"] == "playing"
    lifecycle.restart_rooms()
    lifecycle.now += 500
    result = lifecycle.sync()["game"]
    assert result == {
        "game_id": game_id,
        "status": "completed",
        "end_reason": "completed",
        "ended_at_ms": completion_time,
    }
    finished = lifecycle.lobby()
    assert finished["state"] == "lobby"
    assert finished["expires_at_ms"] == completion_time + 30 * 86_400_000
    lifecycle.now += 1
    assert lifecycle.sync() == {"game": None}
    repeated = lifecycle.lobby()
    assert repeated["revision"] == finished["revision"]
    assert repeated["expires_at_ms"] == finished["expires_at_ms"]
