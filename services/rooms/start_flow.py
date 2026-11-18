"""Durable Rooms reservation and Game acceptance, with no transaction over HTTP."""

import hashlib
import secrets
from dataclasses import dataclass

from pydantic import Field, ValidationError
from starlette.concurrency import run_in_threadpool

from services.rooms.game_client import start_pending
from shared.contracts import (
    RoomSnapshot,
    Settings,
    StartCommand,
    StartedGame,
    StartGame,
)
from shared.errors import DomainError
from shared.start_readiness import require_start_readiness


class RoomStart(StartCommand):
    settings: Settings = Field(default_factory=Settings)


@dataclass(frozen=True)
class Reservation:
    operation_id: str
    payload: StartGame
    game_id: str | None = None


class StartFlow:
    def __init__(self, db, rooms, game_client, clock):
        self.db = db
        self.rooms = rooms
        self.game_client = game_client
        self.clock = clock

    async def start(self, room_id, credential, request):
        try:
            reservation = await run_in_threadpool(
                self.reserve, room_id, credential, request
            )
        except self.db.errors:
            raise start_pending() from None
        return await self.dispatch(reservation)

    async def dispatch(self, reservation):
        if reservation.game_id is not None:
            return StartedGame(game_id=reservation.game_id)
        if self.game_client is None:
            raise start_pending()
        result = await self.game_client.start(reservation.payload)
        try:
            await run_in_threadpool(
                self.confirm, reservation.operation_id, result.game_id
            )
        except self.db.errors:
            raise start_pending() from None
        return result

    def reserve(self, room_id, credential, request):
        key = hashlib.sha256(request.request_id.encode()).hexdigest()
        with self.db.transaction() as conn:
            now = self.clock()
            actor = self.rooms.authenticate(conn, room_id, credential, now)
            if not actor["is_host"]:
                raise DomainError("host_required", "Only the host can start.", 403)
            prior = conn.execute(
                "SELECT id,payload_json,game_id FROM room_start_operations WHERE room_id=? AND request_sha256=?",
                (room_id, key),
            ).fetchone()
            if prior is not None:
                payload = StartGame.model_validate_json(prior["payload_json"])
                if (
                    payload.actor_id != actor["id"]
                    or payload.command.room_revision != request.room_revision
                    or payload.settings != request.settings
                ):
                    raise DomainError(
                        "request_conflict",
                        "This request ID was already used differently.",
                        409,
                    )
                return Reservation(prior["id"], payload, prior["game_id"])
            room = self.rooms.room(conn, room_id, now)
            if room["state"] != "lobby":
                raise DomainError(
                    "game_active", "A game is starting or already running.", 409
                )
            if room["revision"] != request.room_revision:
                raise DomainError(
                    "stale_revision", "The lobby changed. Refresh and try again.", 409
                )
            try:
                snapshot = RoomSnapshot.model_validate(
                    self.rooms.snapshot(conn, room_id)
                )
            except ValidationError:
                raise DomainError(
                    "invalid_start_snapshot",
                    "The room cannot currently provide a valid game roster and song pool.",
                    409,
                ) from None
            require_start_readiness(snapshot.model_dump())
            if self.game_client is None:
                raise DomainError(
                    "game_unavailable", "The Game service is not configured.", 503
                )
            operation_id = secrets.token_hex(16)
            payload = StartGame(
                snapshot=snapshot,
                actor_id=actor["id"],
                settings=request.settings,
                command=StartCommand(
                    request_id=operation_id, room_revision=request.room_revision
                ),
            )
            conn.execute(
                """INSERT INTO room_start_operations (id,room_id,request_sha256,payload_json,created_at_ms)
                   VALUES (?,?,?,?,?)""",
                (operation_id, room_id, key, payload.model_dump_json(), now),
            )
            self.rooms.set_state(conn, room_id, "playing")
            return Reservation(operation_id, payload)

    def confirm(self, operation_id, game_id):
        with self.db.transaction() as conn:
            reservation = conn.execute(
                "SELECT game_id FROM room_start_operations WHERE id=?", (operation_id,)
            ).fetchone()
            if reservation is None or reservation["game_id"] not in (None, game_id):
                raise start_pending()
            if reservation["game_id"] is None:
                conn.execute(
                    "UPDATE room_start_operations SET game_id=? WHERE id=?",
                    (game_id, operation_id),
                )
