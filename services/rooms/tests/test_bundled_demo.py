"""Public Demo admission and media use the same bundled catalog."""

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.rooms.app import create_app
from services.rooms.tests.test_api import admission
from shared.development import ServiceConfig

PACK = Path(__file__).resolve().parents[3] / "catalog" / "bundled_demo"
MEDIA_PREFIX = "/static/demo/local/bundled-v1/"
SERVICE = {"X-Service-Token": "bundled-demo-test-service"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("GAME_BASE_URL", raising=False)
    config = ServiceConfig(
        f"sqlite:///{tmp_path}/rooms.sqlite3", SERVICE["X-Service-Token"]
    )
    with TestClient(create_app(config)) as client:
        yield client


def room_token(player):
    return {"Authorization": f"Bearer {player['token']}"}


def admit(client, path, nickname):
    response = client.post(path, json={"nickname": nickname})
    assert response.status_code == 201, response.text
    return response.json()


def test_public_demo_admission_does_not_accept_forged_listening_data(client):
    response = client.post("/api/rooms", json=admission("Impostor"))
    assert response.status_code == 422
    assert any(
        item["loc"] == ["body", "imported"] for item in response.json()["detail"]
    )
    with client.app.state.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0

    host = admit(client, "/api/rooms", "Host")
    room_path = f"/api/rooms/{host['room_id']}"
    response = client.post(room_path + "/join", json=admission("Impostor"))
    assert response.status_code == 422
    response = client.get(room_path + "/lobby", headers=room_token(host))
    assert response.status_code == 200
    assert [player["id"] for player in response.json()["players"]] == [
        host["player_id"]
    ]


def test_three_player_demo_uses_bundled_catalog_and_serves_clip_ranges(client):
    host = admit(client, "/api/rooms", "Host")
    room_id = host["room_id"]
    room_path = f"/api/rooms/{room_id}"
    guests = [
        admit(client, room_path + "/join", name) for name in ("Guest One", "Guest Two")
    ]
    players = [host, *guests]
    expected_ids = {player["player_id"] for player in players}
    assert len(expected_ids) == 3

    for player in players:
        lobby = client.get(room_path + "/lobby", headers=room_token(player))
        assert lobby.status_code == 200
        assert {entry["id"] for entry in lobby.json()["players"]} == expected_ids
        assert all("listeners" not in entry for entry in lobby.json()["players"])
        assert "songs" not in lobby.json()

    snapshot_path = f"/internal/rooms/{room_id}/snapshot"
    assert (
        client.get(snapshot_path, headers=SERVICE | room_token(guests[0])).status_code
        == 403
    )
    response = client.get(snapshot_path, headers=SERVICE | room_token(host))
    assert response.status_code == 200, response.text
    songs = response.json()["songs"]
    catalog = {
        item["preview_url"]: item
        for item in json.loads((PACK / "demo_catalog.json").read_text())
    }
    assert len(catalog) == 48
    assert {song["preview_url"] for song in songs} <= catalog.keys()
    counts = Counter()
    for song in songs:
        reference = catalog[song["preview_url"]]
        assert song["title"] == reference["title"]
        assert song["pool_kind"] == reference["pool_kind"]
        counts.update(listener["player_id"] for listener in song["listeners"])
        assert bool(song["listeners"]) == (song["pool_kind"] == "personal")
    assert counts == {player_id: 24 for player_id in expected_ids}
    assert sum(song["pool_kind"] == "decoy" for song in songs) == 8

    clip_url = next(
        song["preview_url"] for song in songs if song["pool_kind"] == "personal"
    )
    assert clip_url.startswith(MEDIA_PREFIX)
    clip_name = clip_url.rsplit("/", 1)[-1]
    asset = PACK / "clips" / clip_name
    asset_bytes = asset.read_bytes()
    clip = client.get(clip_url)
    assert clip.status_code == 200
    assert clip.headers["content-type"].startswith("audio/")
    assert hashlib.sha256(clip.content).hexdigest() == catalog[clip_url]["sha256"]
    head = client.head(clip_url)
    assert head.status_code == 200
    assert head.headers["content-length"] == str(len(asset_bytes))
    partial = client.get(clip_url, headers={"Range": "bytes=0-127"})
    assert partial.status_code == 206
    assert partial.headers["content-range"] == f"bytes 0-127/{len(asset_bytes)}"
    assert partial.content == asset_bytes[:128]
