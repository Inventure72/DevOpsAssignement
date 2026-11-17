"""Rooms-only extraction API; provider/browser orchestration follows separately."""

import time
from pathlib import Path
from threading import RLock
from typing import Annotated, Literal

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field

from services.rooms.domain.service import RoomsService
from shared.contracts import Body, ListeningImport, RoomSnapshot
from shared.development import ServiceConfig, service_app
from shared.errors import DomainError


class Admission(Body):
    nickname: Annotated[str, Field(min_length=1, max_length=24)]
    character_id: str = "coral"
    mode: Literal["demo", "normal"] = "demo"
    imported: ListeningImport


class AdmissionResult(Body):
    room_id: str
    code: str
    player_id: str
    token: str


def create_app(config=None, *, clock=None, playtest=False):
    config = config or ServiceConfig.from_env("rooms")
    app, db, internal = service_app(
        "Rooms",
        config,
        Path(__file__).parent / "development",
        required_tables=("rooms", "players", "songs", "player_songs", "demo_catalog"),
    )
    rooms = RoomsService(playtest=playtest)
    clock = clock or (lambda: time.time_ns() // 1_000_000)
    lock = RLock()
    bearer = HTTPBearer(auto_error=False)

    def token(auth: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if auth is None:
            raise DomainError("unauthorized", "A valid room session is required", 401)
        return auth.credentials

    def admitted(result):
        return AdmissionResult(
            room_id=result["room"]["id"],
            code=result["room"]["code"],
            player_id=result["player"]["id"],
            token=result["token"],
        )

    @app.post("/internal/rooms", status_code=201, dependencies=[Depends(internal)])
    def create(payload: Admission) -> AdmissionResult:
        with lock, db.transaction() as conn:
            return admitted(
                rooms.create(
                    conn,
                    payload.nickname,
                    payload.character_id,
                    payload.mode,
                    clock(),
                    imported=payload.imported.model_dump(),
                )
            )

    @app.post(
        "/internal/rooms/{room_id}/join",
        status_code=201,
        dependencies=[Depends(internal)],
    )
    def join(room_id: str, payload: Admission) -> AdmissionResult:
        with lock, db.transaction() as conn:
            room = rooms.room(conn, room_id, clock())
            if room["mode"] != payload.mode:
                raise DomainError("music_source_mismatch", "Room mode differs", 409)
            return admitted(
                rooms.join(
                    conn,
                    room_id,
                    payload.nickname,
                    payload.character_id,
                    clock(),
                    imported=payload.imported.model_dump(),
                )
            )

    @app.get("/api/room-codes/{code}")
    def resolve(code: str) -> dict:
        with db.read() as conn:
            return rooms.resolve_code(conn, code, clock())

    @app.get("/api/rooms/{room_id}/lobby")
    def lobby(room_id: str, credential: str = Depends(token)) -> dict:
        with db.read() as conn:
            now = clock()
            rooms.authenticate(conn, room_id, credential, now)
            return rooms.lobby(conn, room_id, now)

    @app.post("/api/rooms/{room_id}/heartbeat")
    def heartbeat(room_id: str, credential: str = Depends(token)) -> dict:
        with lock, db.transaction() as conn:
            now = clock()
            actor = rooms.authenticate(conn, room_id, credential, now)
            rooms.heartbeat(conn, room_id, actor["id"], now)
            return {"accepted": True, "server_now_ms": now}

    @app.get(
        "/internal/rooms/{room_id}/snapshot",
        dependencies=[Depends(internal)],
    )
    def snapshot(room_id: str, credential: str = Depends(token)) -> RoomSnapshot:
        with lock, db.read() as conn:
            actor = rooms.authenticate(conn, room_id, credential, clock())
            if not actor["is_host"]:
                raise DomainError(
                    "host_required", "Only the host can export a game", 403
                )
            return RoomSnapshot.model_validate(rooms.snapshot(conn, room_id))

    return app


app = create_app()
