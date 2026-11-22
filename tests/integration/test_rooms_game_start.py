"""Start recovery with real service apps and a fault injected at the HTTP boundary."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient

from services.game.app import create_app as create_game
from services.rooms.app import create_app as create_rooms
from services.rooms.game_client import GameClient
from services.rooms.tests.test_api import admission
from shared.development import ServiceConfig

SERVICE_TOKEN = "isolated-integration-service"
GAME_URL = "https://game.test"


def auth(player):
    return {"Authorization": f"Bearer {player['token']}"}


def add_host(client):
    reply = client.post(
        "/internal/rooms",
        json=admission(),
        headers={"X-Service-Token": SERVICE_TOKEN},
    )
    assert reply.status_code == 201, reply.text
    return reply.json()


def join(client, host, name):
    return client.post(
        f"/internal/rooms/{host['room_id']}/join",
        json=admission(name),
        headers={"X-Service-Token": SERVICE_TOKEN},
    )


def lobby(client, host):
    reply = client.get(f"/api/rooms/{host['room_id']}/lobby", headers=auth(host))
    assert reply.status_code == 200, reply.text
    return reply.json()


def start_request(client, host, request_id="start-once"):
    return {
        "request_id": request_id,
        "room_revision": lobby(client, host)["revision"],
        "settings": {"round_count": 5, "decoys_enabled": False},
    }


def test_recover_committed_game_after_lost_response_and_rooms_restart(tmp_path):
    rooms_config = ServiceConfig(f"sqlite:///{tmp_path}/rooms.sqlite3", SERVICE_TOKEN)
    game_config = ServiceConfig(f"sqlite:///{tmp_path}/game.sqlite3", SERVICE_TOKEN)
    game_app = create_game(game_config)
    game_transport = httpx.ASGITransport(app=game_app)
    requests = []
    accepted = []
    now = [1_800_000_000_000]

    async def forward_to_game(request):
        assert request.url.path == "/internal/games"
        assert request.method == "POST"
        assert request.headers["X-Service-Token"] == SERVICE_TOKEN
        assert "Authorization" not in request.headers
        requests.append(json.loads(request.content))
        upstream = await game_transport.handle_async_request(request)
        await upstream.aread()
        assert upstream.status_code == 200, upstream.text
        accepted.append(upstream.json())
        if len(requests) == 1:
            raise httpx.ReadTimeout("Game committed before its response was lost")
        return upstream

    def rooms_app():
        return create_rooms(
            rooms_config,
            clock=lambda: now[0],
            lifecycle_interval=None,
            game_client=GameClient(
                GAME_URL, SERVICE_TOKEN, transport=httpx.MockTransport(forward_to_game)
            ),
        )

    with TestClient(game_app) as game, ExitStack() as rooms_process:
        rooms = rooms_process.enter_context(TestClient(rooms_app()))
        host = add_host(rooms)
        guest_reply = join(rooms, host, "First guest")
        assert guest_reply.status_code == 201, guest_reply.text
        guest = guest_reply.json()
        assert join(rooms, host, "Second guest").status_code == 201
        path = f"/api/rooms/{host['room_id']}/start"
        payload = start_request(rooms, host)
        assert rooms.post(path, headers=auth(guest), json=payload).status_code == 403
        assert not requests
        pending = rooms.post(path, headers=auth(host), json=payload)
        assert pending.status_code == 503, pending.text
        assert pending.json()["error"]["code"] == "game_start_pending"
        assert len(requests) == 1
        assert requests[0]["actor_id"] == host["player_id"]
        assert requests[0]["snapshot"]["revision"] == payload["room_revision"]
        game_id = accepted[0]["game_id"]
        game_view = game.get(
            f"/internal/games/{game_id}",
            params={"actor_id": host["player_id"]},
            headers={"X-Service-Token": SERVICE_TOKEN},
        )
        assert game_view.status_code == 200, game_view.text
        assert game_view.json()["phase"] == "setup"

        rooms_process.close()
        now[0] += 2500
        restarted = rooms_process.enter_context(TestClient(rooms_app()))
        assert restarted.get("/health").status_code == 200
        pending_lobby = lobby(restarted, host)
        assert all(field not in pending_lobby for field in ("songs", "snapshot"))
        assert join(restarted, host, "Late guest").status_code == 409
        changed = {
            **payload,
            "settings": {**payload["settings"], "round_count": 10},
        }
        conflict = restarted.post(path, headers=auth(host), json=changed)
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["error"]["code"] == "request_conflict"
        changed_id = {**payload, "request_id": "unrelated-start"}
        active = restarted.post(path, headers=auth(host), json=changed_id)
        assert active.status_code == 409, active.text
        assert active.json()["error"]["code"] == "game_active"
        assert (
            restarted.post(
                f"/api/rooms/{host['room_id']}/heartbeat", headers=auth(host)
            ).status_code
            == 200
        )
        assert len(requests) == 1

        recovered = restarted.post(path, headers=auth(host), json=payload)
        assert recovered.status_code == 200, recovered.text
        assert recovered.json() == {"game_id": game_id}
        assert len(requests) == 2
        assert requests[1] == requests[0]
        assert accepted == [recovered.json(), recovered.json()]
        assert join(restarted, host, "Late guest").status_code == 409
        duplicate = restarted.post(path, headers=auth(host), json=payload)
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json() == recovered.json()
        assert len(requests) == 2
        with game.app.state.db.read() as conn:
            assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1


def test_outbound_start_deadline_keeps_reservation_pending(tmp_path):
    config = ServiceConfig(f"sqlite:///{tmp_path}/rooms.sqlite3", SERVICE_TOKEN)
    contacted = []

    async def slow_reply(request):
        contacted.append(request.url.path)
        await asyncio.sleep(0.3)
        return httpx.Response(200, json={"game_id": "a" * 32})

    client = GameClient(
        GAME_URL,
        SERVICE_TOKEN,
        timeout=0.05,
        transport=httpx.MockTransport(slow_reply),
    )
    with TestClient(
        create_rooms(config, game_client=client, lifecycle_interval=None)
    ) as rooms:
        host = add_host(rooms)
        assert join(rooms, host, "First guest").status_code == 201
        assert join(rooms, host, "Second guest").status_code == 201
        response = rooms.post(
            f"/api/rooms/{host['room_id']}/start",
            headers=auth(host),
            json=start_request(rooms, host),
        )
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "game_start_pending"
        assert contacted == ["/internal/games"]
        assert join(rooms, host, "Late guest").status_code == 409


def test_start_leaves_room_database_available_while_game_response_waits(tmp_path):
    config = ServiceConfig(f"sqlite:///{tmp_path}/rooms.sqlite3", SERVICE_TOKEN)
    contacted, release = Event(), Event()

    async def wait_for_release(request):
        contacted.set()
        assert await asyncio.to_thread(release.wait, 3)
        return httpx.Response(200, json={"game_id": "b" * 32})

    client = GameClient(
        GAME_URL, SERVICE_TOKEN, transport=httpx.MockTransport(wait_for_release)
    )
    with (
        TestClient(
            create_rooms(config, game_client=client, lifecycle_interval=None)
        ) as rooms,
        ThreadPoolExecutor(max_workers=1) as worker,
    ):
        host = add_host(rooms)
        assert join(rooms, host, "First guest").status_code == 201
        assert join(rooms, host, "Second guest").status_code == 201
        payload = start_request(rooms, host)
        started = worker.submit(
            rooms.post,
            f"/api/rooms/{host['room_id']}/start",
            headers=auth(host),
            json=payload,
        )
        try:
            assert contacted.wait(2)
            assert rooms.get("/health").status_code == 200
            assert join(rooms, host, "Late guest").status_code == 409
            assert not started.done()
        finally:
            release.set()
        assert started.result(timeout=2).json() == {"game_id": "b" * 32}


def test_start_recovers_after_rooms_confirmation_write_fails(tmp_path, monkeypatch):
    config = ServiceConfig(f"sqlite:///{tmp_path}/rooms.sqlite3", SERVICE_TOKEN)
    received = []

    async def game_accepts(request):
        received.append(json.loads(request.content))
        return httpx.Response(200, json={"game_id": "c" * 32})

    client = GameClient(
        GAME_URL, SERVICE_TOKEN, transport=httpx.MockTransport(game_accepts)
    )
    app = create_rooms(config, game_client=client, lifecycle_interval=None)
    confirm = app.state.db.transaction
    with TestClient(app) as rooms:
        host = add_host(rooms)
        assert join(rooms, host, "First guest").status_code == 201
        assert join(rooms, host, "Second guest").status_code == 201
        path = f"/api/rooms/{host['room_id']}/start"
        payload = start_request(rooms, host)
        attempts = 0

        def fail_second_transaction():
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise OSError("could not persist the Game response")
            return confirm()

        with monkeypatch.context() as patch:
            patch.setattr(app.state.db, "transaction", fail_second_transaction)
            response = rooms.post(path, headers=auth(host), json=payload)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "game_start_pending"
        assert rooms.post(path, headers=auth(host), json=payload).json() == {
            "game_id": "c" * 32
        }
        assert len(received) == 2 and received[0] == received[1]


@pytest.mark.parametrize(
    ("request_case", "expected_status", "expected_code"),
    [
        ("stale", 409, "stale_revision"),
        ("too_few_players", 400, "player_count"),
        ("too_few_songs", 400, "insufficient_songs"),
    ],
)
def test_reject_invalid_start_before_contacting_peer(
    tmp_path, request_case, expected_status, expected_code
):
    config = ServiceConfig(f"sqlite:///{tmp_path}/rooms.sqlite3", SERVICE_TOKEN)

    def unexpected_request(request):
        pytest.fail(f"Invalid Start reached the peer: {request.url.path}")

    app = create_rooms(
        config,
        lifecycle_interval=None,
        clock=lambda: 1_800_000_000_000,
        game_client=GameClient(
            GAME_URL, SERVICE_TOKEN, transport=httpx.MockTransport(unexpected_request)
        ),
    )
    with TestClient(app) as rooms:
        host = add_host(rooms)
        if request_case != "too_few_players":
            assert join(rooms, host, "First guest").status_code == 201
            second_guest = admission("Second guest")
            if request_case == "too_few_songs":
                second_guest["imported"]["songs"] = second_guest["imported"]["songs"][
                    :9
                ]
            joined = rooms.post(
                f"/internal/rooms/{host['room_id']}/join",
                json=second_guest,
                headers={"X-Service-Token": SERVICE_TOKEN},
            )
            assert joined.status_code == 201, joined.text
        payload = start_request(rooms, host)
        if request_case == "stale":
            payload["room_revision"] -= 1
        reply = rooms.post(
            f"/api/rooms/{host['room_id']}/start", json=payload, headers=auth(host)
        )
        assert reply.status_code == expected_status, reply.text
        assert reply.json()["error"]["code"] == expected_code
        assert join(rooms, host, "Late guest").status_code == 201
