"""Rooms-only extraction API; provider/browser orchestration follows separately."""

import os
import time
from pathlib import Path
from threading import RLock
from typing import Annotated, Literal

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from services.rooms.domain.service import RoomsService
from services.rooms.game_client import GameClient
from services.rooms.lifecycle import LeaveRoom, LeftRoom, RoomGame, RoomLifecycle
from services.rooms.start_flow import RoomStart, StartFlow
from services.rooms.storage import REQUIRED_TABLES, RoomsDatabase
from shared.contracts import Body, ListeningImport, RoomSnapshot, StartedGame
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


def create_app(
    config=None, *, clock=None, playtest=False, game_client=None, lifecycle_interval=1.0
):
    config = config or ServiceConfig.from_env("rooms", default_database_url="")
    database = (
        None
        if config.database_url.startswith("sqlite:///")
        else RoomsDatabase(config.database_url)
    )
    peer_url = os.environ.get("GAME_BASE_URL")
    if game_client is None and peer_url and config.service_token:
        game_client = GameClient(peer_url, config.service_token)
    app, db, internal = service_app(
        "Rooms",
        config,
        Path(__file__).parent / "development",
        required_tables=REQUIRED_TABLES,
        database=database,
        worker=(lambda: lifecycle.run(lifecycle_interval))
        if game_client is not None and lifecycle_interval is not None
        else None,
    )
    rooms = RoomsService(playtest=playtest)
    clock = clock or (lambda: time.time_ns() // 1_000_000)
    start_flow = StartFlow(db, rooms, game_client, clock)
    lifecycle = RoomLifecycle(db, rooms, game_client, start_flow, clock)
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

    def heartbeat_transaction(room_id, credential):
        with lock, db.transaction() as conn:
            now = clock()
            actor = rooms.authenticate(conn, room_id, credential, now)
            rooms.heartbeat(conn, room_id, actor["id"], now)
            return {"accepted": True, "server_now_ms": now}

    @app.post("/api/rooms/{room_id}/heartbeat")
    async def heartbeat(room_id: str, credential: str = Depends(token)) -> dict:
        try:
            return await run_in_threadpool(heartbeat_transaction, room_id, credential)
        except DomainError as error:
            if error.code != "host_expired" or game_client is None:
                raise
        await lifecycle.sync(room_id, credential)
        return await run_in_threadpool(heartbeat_transaction, room_id, credential)

    @app.post("/api/rooms/{room_id}/game/sync")
    async def sync_game(room_id: str, credential: str = Depends(token)) -> RoomGame:
        return await lifecycle.sync(room_id, credential)

    @app.post("/api/rooms/{room_id}/leave")
    async def leave(
        room_id: str, payload: LeaveRoom, credential: str = Depends(token)
    ) -> LeftRoom:
        return await lifecycle.leave(room_id, credential, payload)

    @app.post("/api/rooms/{room_id}/start")
    async def start(
        room_id: str, payload: RoomStart, credential: str = Depends(token)
    ) -> StartedGame:
        return await start_flow.start(room_id, credential, payload)

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
