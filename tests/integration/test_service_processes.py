"""Run real independent processes; no monolith or user database is started."""

import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from services.game.storage import GameDatabase
from services.rooms.storage import RoomsDatabase
from services.rooms.tests.test_api import admission
from tests.support.sqlserver import guarded_database

ROOT = Path(__file__).resolve().parents[2]


def request(base, path, *, payload=None, headers=None):
    data = None if payload is None else json.dumps(payload).encode()
    values = {"Content-Type": "application/json", **(headers or {})}
    call = Request(base + path, data=data, headers=values)
    try:
        with urlopen(call, timeout=2) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        with error:
            return error.code, json.load(error)


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def verify_process_start(tmp_path, database_urls):
    service_token = secrets.token_urlsafe(32)
    headers = {"X-Service-Token": service_token}
    processes, urls = {}, {}
    with ExitStack() as stack:
        for name in ("game", "rooms"):
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            environment = {
                **os.environ,
                "DATABASE_URL": database_urls[name],
                "SERVICE_TOKEN": service_token,
                "PORT": str(port),
                "GIT_SHA": "process-test",
            }
            if name == "rooms":
                environment["GAME_BASE_URL"] = urls["game"]
            log = stack.enter_context((tmp_path / f"{name}.log").open("w+"))
            error_log = stack.enter_context(
                (tmp_path / f"{name}.stderr.log").open("w+")
            )
            process = subprocess.Popen(
                [sys.executable, "-m", f"services.{name}"],
                cwd=ROOT,
                env=environment,
                stdout=log,
                stderr=error_log,
            )
            stack.callback(stop, process)
            processes[name] = process
            urls[name] = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 15
            while True:
                if process.poll() is not None or time.monotonic() >= deadline:
                    pytest.fail(
                        f"{name} failed to become ready\n"
                        f"{(tmp_path / f'{name}.log').read_text()}\n"
                        f"{(tmp_path / f'{name}.stderr.log').read_text()}"
                    )
                try:
                    status, health = request(urls[name], "/health")
                    if status == 200:
                        assert health["version"] == "process-test"
                        break
                except (URLError, TimeoutError):
                    pass
                time.sleep(0.05)

        status, host = request(
            urls["rooms"], "/internal/rooms", payload=admission(), headers=headers
        )
        assert status == 201
        guests = []
        body_marker = "test-body-" + secrets.token_hex(5)
        for nickname in (body_marker, "Guest 2"):
            joined, guest = request(
                urls["rooms"],
                f"/internal/rooms/{host['room_id']}/join",
                payload=admission(nickname),
                headers=headers,
            )
            assert joined == 201
            guests.append(guest)
        room_auth = {"Authorization": f"Bearer {host['token']}"}
        lobby_status, lobby = request(
            urls["rooms"],
            f"/api/rooms/{host['room_id']}/lobby",
            headers=room_auth,
        )
        assert lobby_status == 200
        start_path = f"/api/rooms/{host['room_id']}/start"
        payload = {
            "request_id": "process-start",
            "room_revision": lobby["revision"],
            "settings": {"round_count": 5, "decoys_enabled": False},
        }
        status, started = request(
            urls["rooms"],
            start_path,
            headers=room_auth,
            payload=payload,
        )
        assert status == 200, started
        assert request(
            urls["rooms"], start_path, headers=room_auth, payload=payload
        ) == (200, started)
        game_path = f"/internal/games/{started['game_id']}?actor_id={host['player_id']}"
        assert request(urls["game"], game_path, headers=headers)[1]["phase"] == "setup"
        room_path = f"/api/rooms/{host['room_id']}"
        guest_auth = {"Authorization": f"Bearer {guests[0]['token']}"}
        synchronized, active = request(
            urls["rooms"], room_path + "/game/sync", headers=guest_auth, payload={}
        )
        assert synchronized == 200 and active["game"]["game_id"] == started["game_id"]
        assert active["game"]["status"] == "preparing"
        leave = {"request_id": "process-leave", "game_id": started["game_id"]}
        assert request(
            urls["rooms"], room_path + "/leave", headers=guest_auth, payload=leave
        ) == (200, {"accepted": True})
        assert request(
            urls["rooms"], room_path + "/leave", headers=room_auth, payload=leave
        ) == (200, {"accepted": True})
        assert request(
            urls["rooms"], room_path + "/leave", headers=room_auth, payload=leave
        ) == (200, {"accepted": True})
        assert (
            request(urls["game"], game_path, headers=headers)[1]["phase"] == "finished"
        )
        assert request(
            urls["rooms"], room_path + "/game/sync", headers=guest_auth, payload={}
        ) == (200, {"game": None})
        lobby_status, lobby = request(
            urls["rooms"], room_path + "/lobby", headers=room_auth
        )
        assert (
            lobby_status == 200
            and lobby["state"] == "lobby"
            and len(lobby["players"]) == 3
        )
        payload.update(room_revision=lobby["revision"], request_id="process-next-game")
        status, restarted = request(
            urls["rooms"], start_path, headers=room_auth, payload=payload
        )
        assert status == 200 and restarted["game_id"] != started["game_id"]
        assert request(
            urls["rooms"], room_path + "/leave", headers=room_auth, payload=leave
        ) == (200, {"accepted": True})
        synchronized, active = request(
            urls["rooms"], room_path + "/game/sync", headers=guest_auth, payload={}
        )
        assert synchronized == 200 and active["game"]["game_id"] == restarted["game_id"]
        ended, _ = request(
            urls["game"],
            f"/internal/games/{restarted['game_id']}/end",
            headers=headers,
            payload={
                "actor_id": host["player_id"],
                "request_id": "finish-without-rooms-command",
            },
        )
        assert ended == 200
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            lobby_status, lobby = request(
                urls["rooms"], room_path + "/lobby", headers=room_auth
            )
            assert lobby_status == 200
            if lobby["state"] == "lobby":
                break
            time.sleep(0.05)
        else:
            pytest.fail("Rooms worker did not recover the terminal Game outcome")
        stop(processes["rooms"])
        assert request(urls["game"], "/health")[0] == 200
        assert request(urls["game"], game_path, headers=headers)[0] == 200

        query_marker = "test-query-" + secrets.token_hex(8)
        path_marker = "test-path-" + secrets.token_hex(8)
        header_marker = "test-header-" + secrets.token_hex(8)
        assert (
            request(
                urls["game"],
                f"/{path_marker}?search={query_marker}",
                headers={"Authorization": f"Bearer {header_marker}"},
            )[0]
            == 404
        )
        assert (
            request(
                urls["game"], game_path, headers={"X-Service-Token": header_marker}
            )[0]
            == 401
        )
        stop(processes["game"])

        stdout = {name: (tmp_path / f"{name}.log").read_text() for name in processes}
        for owner, verb, path, status in (
            ("rooms", "GET", "/api/rooms/{room_id}/lobby", 200),
            ("rooms", "POST", "/internal/rooms/{room_id}/join", 201),
            ("game", "POST", "/internal/games", 200),
            ("game", "GET", "/internal/games/{game_id}", 401),
            ("game", "GET", "<unmatched>", 404),
        ):
            prefix = f"service={owner} method={verb} path={path} status={status}"
            assert re.search(
                re.escape(prefix) + r" duration_ms=\d+\.\d{2}$", stdout[owner], re.M
            )
        for owner in processes:
            output = stdout[owner] + (tmp_path / f"{owner}.stderr.log").read_text()
            assert "HTTP Request:" not in output
            for value in (
                body_marker,
                query_marker,
                path_marker,
                header_marker,
                service_token,
                host["token"],
                host["room_id"],
                started["game_id"],
            ):
                assert value not in output, f"{owner} log leaked a request value"


def test_two_service_processes_own_data_and_peer_independent_health(tmp_path):
    verify_process_start(
        tmp_path,
        {
            name: f"sqlite:///{tmp_path / (name + '.sqlite3')}"
            for name in ("rooms", "game")
        },
    )
    assert {p.name for p in tmp_path.glob("*.sqlite3")} == {
        "rooms.sqlite3",
        "game.sqlite3",
    }


def test_two_sql_server_service_processes_reconcile_lifecycle(tmp_path):
    database_urls = {
        name: os.environ.get(f"{name.upper()}_TEST_DATABASE_URL")
        for name in ("rooms", "game")
    }
    if not all(database_urls.values()):
        pytest.skip("Run python -m tools.test_rooms_sqlserver for SQL Server")
    for name, factory in (("rooms", RoomsDatabase), ("game", GameDatabase)):
        guarded_database(
            database_urls[name],
            os.environ.get(f"{name.upper()}_TEST_GUARD"),
            factory,
            name,
        )
    verify_process_start(tmp_path, database_urls)


@pytest.mark.parametrize("service,peer", [("rooms", "game"), ("game", "rooms")])
def test_service_import_does_not_load_peer_or_monolith(service, peer):
    code = (
        f"import services.{service}.app; import sys; "
        "assert 'backend.app' not in sys.modules; "
        f"assert not any(n.startswith('services.{peer}') for n in sys.modules)"
    )
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True, timeout=10)
