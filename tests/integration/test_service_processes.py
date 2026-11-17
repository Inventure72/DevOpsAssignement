"""Run real independent processes; no monolith or user database is started."""

import json
import os
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

from services.rooms.tests.test_api import admission

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


def test_two_service_processes_own_data_and_peer_independent_health(tmp_path):
    service_token = secrets.token_urlsafe(32)
    headers = {"X-Service-Token": service_token}
    processes, urls = {}, {}
    with ExitStack() as stack:
        for name in ("rooms", "game"):
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            environment = {
                **os.environ,
                "DATABASE_URL": f"sqlite:///{tmp_path / (name + '.sqlite3')}",
                "SERVICE_TOKEN": service_token,
                "PORT": str(port),
                "GIT_SHA": "process-test",
            }
            log = stack.enter_context((tmp_path / f"{name}.log").open("w+"))
            process = subprocess.Popen(
                [sys.executable, "-m", f"services.{name}"],
                cwd=ROOT,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            stack.callback(stop, process)
            processes[name] = process
            urls[name] = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 15
            while True:
                if process.poll() is not None or time.monotonic() >= deadline:
                    log.flush()
                    pytest.fail(
                        f"{name} failed to become ready: {(tmp_path / f'{name}.log').read_text()}"
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
        for nickname in ("Guest 1", "Guest 2"):
            assert (
                request(
                    urls["rooms"],
                    f"/internal/rooms/{host['room_id']}/join",
                    payload=admission(nickname),
                    headers=headers,
                )[0]
                == 201
            )
        status, snapshot = request(
            urls["rooms"],
            f"/internal/rooms/{host['room_id']}/snapshot",
            headers=headers | {"Authorization": f"Bearer {host['token']}"},
        )
        assert status == 200
        status, started = request(
            urls["game"],
            "/internal/games",
            headers=headers,
            payload={
                "snapshot": snapshot,
                "actor_id": host["player_id"],
                "settings": {"round_count": 5, "decoys_enabled": False},
                "command": {
                    "request_id": "process-start",
                    "room_revision": snapshot["revision"],
                },
            },
        )
        assert status == 200
        game_path = f"/internal/games/{started['game_id']}?actor_id={host['player_id']}"
        assert request(urls["game"], game_path, headers=headers)[1]["phase"] == "setup"
        # Stopping the peer cannot make own-database health fail or prevent a
        # read of Game's frozen facts. This is not yet HTTP orchestration at Start.
        stop(processes["rooms"])
        assert request(urls["game"], "/health")[0] == 200
        assert request(urls["game"], game_path, headers=headers)[0] == 200
    assert {p.name for p in tmp_path.glob("*.sqlite3")} == {
        "rooms.sqlite3",
        "game.sqlite3",
    }


@pytest.mark.parametrize("service,peer", [("rooms", "game"), ("game", "rooms")])
def test_service_import_does_not_load_peer_or_monolith(service, peer):
    code = (
        f"import services.{service}.app; import sys; "
        "assert 'backend.app' not in sys.modules; "
        f"assert not any(n.startswith('services.{peer}') for n in sys.modules)"
    )
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True, timeout=10)
