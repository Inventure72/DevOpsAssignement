"""Public browser routes against both standalone service apps."""

from contextlib import ExitStack

import httpx
from fastapi.testclient import TestClient

from services.game.app import create_app as create_game
from services.game.storage import REQUIRED_TABLES as GAME_TABLES
from services.rooms.app import create_app as create_rooms
from services.rooms.game_client import GameClient
from services.rooms.storage import REQUIRED_TABLES as ROOM_TABLES
from shared.development import ServiceConfig

ORIGIN = "https://ui.test"
SERVICE_TOKEN = "browser-integration-service"


def bearer(token):
    return {"Origin": ORIGIN, "Authorization": f"Bearer {token}"}


def request(client, verb, path, status=200, **kwargs):
    result = client.request(verb, path, **kwargs)
    assert result.status_code == status, (verb, path, result.status_code, status)
    return result


def stored_rows(client, tables):
    with client.app.state.db.read() as conn:
        return {
            table: {
                tuple(dict(row).items())
                for row in conn.execute(f"SELECT * FROM {table}")
            }
            for table in tables
        }


def test_split_browser_sessions_audio_and_scoring(tmp_path):
    exercise_split_browser_api(
        f"sqlite:///{tmp_path}/rooms.sqlite3", f"sqlite:///{tmp_path}/game.sqlite3"
    )


def exercise_split_browser_api(rooms_url, game_url):
    now = 1_800_000_000_000

    def clock():
        return now

    game_app = create_game(
        ServiceConfig(game_url, SERVICE_TOKEN, ui_origin=ORIGIN), clock=clock
    )
    rooms_app = create_rooms(
        ServiceConfig(rooms_url, SERVICE_TOKEN, ui_origin=ORIGIN),
        clock=clock,
        game_client=GameClient(
            "https://game.test",
            SERVICE_TOKEN,
            transport=httpx.ASGITransport(app=game_app),
        ),
        lifecycle_interval=None,
    )
    with ExitStack() as stack:
        rooms = stack.enter_context(TestClient(rooms_app))
        game = stack.enter_context(TestClient(game_app))
        for client in (rooms, game):
            preflight = {
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization, content-type",
            }
            allowed = request(
                client, "OPTIONS", "/health", headers=preflight | {"Origin": ORIGIN}
            )
            assert allowed.headers["access-control-allow-origin"] == ORIGIN
            for origin in ("https://other.test", "null"):
                refused = request(
                    client,
                    "OPTIONS",
                    "/health",
                    400,
                    headers=preflight | {"Origin": origin},
                )
                assert "access-control-allow-origin" not in refused.headers
            request(
                client,
                "OPTIONS",
                "/health",
                400,
                headers=preflight
                | {
                    "Origin": ORIGIN,
                    "Access-Control-Request-Headers": "x-service-token",
                },
            )

        host = request(
            rooms, "POST", "/api/rooms", 201, json={"nickname": "Host"}
        ).json()
        base = f"/api/rooms/{host['room_id']}"
        invite = request(rooms, "GET", f"/api/room-codes/{host['code']}").json()
        assert invite["room_id"] == host["room_id"] and invite["join_available"]
        guests = [
            request(rooms, "POST", base + "/join", 201, json={"nickname": name}).json()
            for name in ("Guest one", "Guest two")
        ]
        people = [host, *guests]
        room_headers = [bearer(person["token"]) for person in people]
        lobby = request(rooms, "GET", base + "/lobby", headers=room_headers[0]).json()
        identifier = request(
            rooms,
            "POST",
            base + "/start",
            headers=room_headers[0],
            json={
                "request_id": "browser-start",
                "room_revision": lobby["revision"],
                "settings": {"round_count": 5, "answer_seconds": 20},
            },
        ).json()["game_id"]
        game_path = f"/api/games/{identifier}"
        room_game = base + "/games/" + identifier
        stranger = request(
            rooms, "POST", "/api/rooms", 201, json={"nickname": "Other host"}
        ).json()
        request(
            rooms,
            "POST",
            room_game + "/session",
            401,
            headers=bearer(stranger["token"]),
        )
        request(
            rooms,
            "POST",
            f"/api/rooms/{stranger['room_id']}/games/{identifier}/session",
            404,
            headers=bearer(stranger["token"]),
        )

        def game_header(room_header):
            grant = request(rooms, "POST", room_game + "/session", headers=room_header)
            assert grant.headers["cache-control"] == "no-store"
            assert grant.json()["expires_in_seconds"] == 60
            return bearer(grant.json()["token"])

        game_headers = [game_header(header) for header in room_headers]
        request(game, "GET", game_path, 401, headers=room_headers[0])
        request(game, "GET", "/api/games/" + "f" * 32, 401, headers=game_headers[0])
        request(game, "GET", game_path + "/audio", 403, headers=game_headers[1])
        request(
            game,
            "GET",
            f"/internal/games/{identifier}?actor_id={host['player_id']}",
            401,
            headers=game_headers[0],
        )
        manifest = request(game, "GET", game_path + "/audio", headers=game_headers[0])
        candidates = manifest.json()["candidates"]
        assert len(candidates) == 20
        for index, candidate in enumerate(candidates):
            payload = {
                "candidate_id": candidate["candidate_id"],
                "request_id": f"check-{index}",
                "ok": True,
            }
            if index == 0:
                clip = request(
                    rooms, "GET", candidate["preview_url"], headers={"Origin": ORIGIN}
                )
                assert clip.headers["access-control-allow-origin"] == ORIGIN
                assert clip.headers["content-type"] == "audio/mpeg"
                request(
                    game,
                    "POST",
                    game_path + "/preload",
                    403,
                    headers=game_headers[1],
                    json=payload,
                )
            request(
                game,
                "POST",
                game_path + "/preload",
                headers=game_headers[0],
                json=payload,
            )
        now += 5000
        request(rooms, "POST", base + "/game/sync", headers=room_headers[0])
        host_view = request(game, "GET", game_path, headers=game_headers[0]).json()
        guest_view = request(game, "GET", game_path, headers=game_headers[1]).json()
        assert host_view["phase"] == "ready"
        assert "audio_candidate_id" in host_view["round"]
        assert "audio_candidate_id" not in guest_view["round"]
        assert host_view["server_now_ms"] == now

        attempt = host_view["round"]
        round_path = game_path + "/rounds/" + attempt["id"]
        for headers in game_headers:
            request(
                game,
                "POST",
                round_path + "/ready",
                headers=headers,
                json={"readiness_generation": attempt["readiness_generation"]},
            )
        request(rooms, "POST", base + "/game/sync", headers=room_headers[0])
        countdown = request(game, "GET", game_path, headers=game_headers[0]).json()
        assert countdown["phase"] == "countdown"
        now = countdown["phase_ends_at_ms"] + 1
        room_rows = stored_rows(rooms, ROOM_TABLES)
        game_rows = stored_rows(game, GAME_TABLES)

        # The countdown is overdue. GETs must still project the stored state.
        assert (
            request(rooms, "GET", base + "/lobby", headers=room_headers[0]).json()[
                "state"
            ]
            == "playing"
        )
        overdue = request(game, "GET", game_path, headers=game_headers[0]).json()
        assert overdue["phase"] == "countdown"
        assert overdue["server_now_ms"] > overdue["phase_ends_at_ms"]
        assert (
            request(game, "GET", game_path + "/audio", headers=game_headers[0]).json()
            == manifest.json()
        )
        assert stored_rows(rooms, ROOM_TABLES) == room_rows
        assert stored_rows(game, GAME_TABLES) == game_rows

        request(rooms, "POST", base + "/game/sync", headers=room_headers[0])
        assert (
            request(game, "GET", game_path, headers=game_headers[1]).json()["phase"]
            == "answering"
        )
        assert stored_rows(rooms, ROOM_TABLES) == room_rows
        advanced = stored_rows(game, GAME_TABLES)
        assert {
            table for table in GAME_TABLES if advanced[table] != game_rows[table]
        } == {"games", "rounds"}
        entry = request(
            rooms,
            "GET",
            room_game + "/songs?q=after+the+ferry",
            headers=room_headers[0],
        ).json()["songs"][0]
        assert set(entry) == {"title", "artist", "selection"}
        answer = {"song_selection": entry["selection"], "who_player_ids": []}
        request(
            game,
            "POST",
            round_path + "/answers",
            400,
            headers=game_headers[1],
            json=answer,
        )
        request(
            game,
            "POST",
            round_path + "/answers",
            400,
            headers=game_headers[0],
            json={**answer, "song_selection": "forged." + entry["selection"]},
        )
        receipt = request(
            game, "POST", round_path + "/answers", headers=game_headers[0], json=answer
        ).json()
        for headers in game_headers[1:]:
            request(
                game,
                "POST",
                round_path + "/answers",
                headers=headers,
                json={"song_selection": None, "who_player_ids": []},
            )
        assert (
            request(
                game,
                "POST",
                round_path + "/answers",
                headers=game_headers[0],
                json=answer,
            ).json()
            == receipt
        )
        reveal = request(game, "GET", game_path, headers=game_headers[0]).json()
        assert reveal["phase"] == "reveal"
        assert (
            reveal["round"]["reveal"]["my_answer"]["song_guess"]["title"]
            == entry["title"]
        )
        assert len(reveal["leaderboard"]) == 3

        now += 60_000
        request(game, "GET", game_path, 401, headers=game_headers[0])
        request(game, "GET", game_path, headers=game_header(room_headers[0]))
