"""Reconcile Rooms after confirmed Game results, without sharing a transaction."""

import asyncio
import logging
from dataclasses import dataclass

from starlette.concurrency import run_in_threadpool

from services.rooms.game_client import lifecycle_unavailable
from services.rooms.start_flow import Reservation
from shared.contracts import (
    Body,
    GameID,
    GameOutcome,
    HostLeave,
    Identifier,
    LivePresence,
    ReconcileGame,
    StartGame,
)
from shared.errors import DomainError

logger = logging.getLogger(__name__)


class LeaveRoom(Body):
    request_id: Identifier
    game_id: GameID | None = None


class RoomGame(Body):
    game: GameOutcome | None


class LeftRoom(Body):
    accepted: bool = True


@dataclass(frozen=True)
class LockedRoom:
    revision: int
    presence: LivePresence
    operation_id: str | None
    game_id: str | None
    saved_start: str | None

    def matches(self, report):
        return (
            report.room_revision + 1 == self.revision
            and self.game_id in (None, report.game_id)
            and self.operation_id in (None, report.operation_id)
        )


class RoomLifecycle:
    def __init__(self, db, rooms, game_client, start, clock):
        self.db = db
        self.rooms = rooms
        self.game_client = game_client
        self.start = start
        self.clock = clock

    def _locked_room(self, conn, room, now):
        if room["state"] != "playing":
            return None
        room_id = room["id"]
        host = self.rooms.host_presence(conn, room_id, now)
        operation = conn.execute(
            """SELECT id,game_id,payload_json FROM room_start_operations
               WHERE room_id=? AND reconciled_at_ms IS NULL""",
            (room_id,),
        ).fetchone()
        return LockedRoom(
            room["revision"],
            LivePresence(
                host_connected=host["connected"],
                host_expired=now >= host["expires_at_ms"],
                connected_player_ids=sorted(
                    self.rooms.connected_player_ids(conn, room_id, now)
                ),
            ),
            operation["id"] if operation else None,
            operation["game_id"] if operation else None,
            operation["payload_json"]
            if operation and operation["game_id"] is None
            else None,
        )

    def _inspect(self, room_id, credential):
        with self.db.transaction() as conn:
            now = self.clock()
            if credential is not None:
                self.rooms.authenticate(conn, room_id, credential, now)
            return self._locked_room(conn, self.rooms.room(conn, room_id, now), now)

    def _apply(self, room_id, captured, report):
        if (
            captured is None
            or report.status not in {"completed", "aborted"}
            or not captured.matches(report)
        ):
            return
        with self.db.transaction() as conn:
            room = self.rooms.repo.room(conn, room_id)
            if (
                room is None
                or room["state"] != "playing"
                or room["revision"] != captured.revision
            ):
                return
            if captured.operation_id is not None:
                operation = conn.execute(
                    "SELECT game_id,reconciled_at_ms FROM room_start_operations WHERE id=? AND room_id=?",
                    (captured.operation_id, room_id),
                ).fetchone()
                if (
                    operation is None
                    or operation["reconciled_at_ms"] is not None
                    or operation["game_id"] not in (None, report.game_id)
                ):
                    return
                conn.execute(
                    "UPDATE room_start_operations SET game_id=?,reconciled_at_ms=? WHERE id=?",
                    (report.game_id, self.clock(), captured.operation_id),
                )
            if report.status == "completed":
                ended_at = max(
                    report.ended_at_ms,
                    room["last_completed_at_ms"] or room["created_at_ms"],
                )
                self.rooms.complete(conn, room_id, ended_at)
            else:
                self.rooms.set_state(conn, room_id, "lobby")

    async def sync(self, room_id, credential=None):
        try:
            locked = await run_in_threadpool(self._inspect, room_id, credential)
        except self.db.errors:
            raise lifecycle_unavailable() from None
        if locked is None:
            return RoomGame(game=None)
        if self.game_client is None or locked.revision < 1:
            raise lifecycle_unavailable()
        game_id = locked.game_id
        if locked.saved_start is not None:
            try:
                result = await self.start.dispatch(
                    Reservation(
                        locked.operation_id,
                        StartGame.model_validate_json(locked.saved_start),
                    )
                )
            except (DomainError, ValueError):
                raise lifecycle_unavailable() from None
            game_id = result.game_id
        report = await self.game_client.reconcile(
            room_id,
            ReconcileGame(
                game_id=game_id,
                room_revision=locked.revision - 1,
                operation_id=locked.operation_id,
                presence=locked.presence,
            ),
        )
        try:
            await run_in_threadpool(self._apply, room_id, locked, report)
        except self.db.errors:
            raise lifecycle_unavailable() from None
        return RoomGame(
            game=GameOutcome(
                game_id=report.game_id,
                status=report.status,
                ended_at_ms=report.ended_at_ms,
                end_reason=report.end_reason,
            )
        )

    def _prepare_leave(self, room_id, credential, request):
        with self.db.transaction() as conn:
            now = self.clock()
            actor = self.rooms.authenticate(conn, room_id, credential, now)
            room = self.rooms.room(conn, room_id, now)
            if request.game_id is None and room["state"] == "playing":
                raise DomainError(
                    "game_identity_required",
                    "Sync the room before leaving a game.",
                    409,
                )
            if not actor["is_host"]:
                if request.game_id is None:
                    self.rooms.remove_player(conn, room_id, actor["id"], now)
                return None, None
            if request.game_id is None:
                return None, None
            return actor["id"], self._locked_room(conn, room, now)

    async def leave(self, room_id, credential, request):
        try:
            actor, locked = await run_in_threadpool(
                self._prepare_leave, room_id, credential, request
            )
        except self.db.errors:
            raise lifecycle_unavailable() from None
        if actor is None:
            return LeftRoom()
        if self.game_client is None:
            raise lifecycle_unavailable()
        current = locked is not None and locked.game_id == request.game_id
        report = await self.game_client.reconcile(
            room_id,
            ReconcileGame(
                game_id=request.game_id,
                leave=HostLeave(actor_id=actor, request_id=request.request_id),
                presence=locked.presence if current else None,
                operation_id=locked.operation_id if current else None,
                room_revision=locked.revision - 1 if current else None,
            ),
        )
        if report.status not in {"completed", "aborted"}:
            raise lifecycle_unavailable()
        try:
            await run_in_threadpool(self._apply, room_id, locked, report)
        except self.db.errors:
            raise lifecycle_unavailable() from None
        return LeftRoom()

    def _playing_ids(self):
        with self.db.read() as conn:
            return [
                row["id"]
                for row in conn.execute("SELECT id FROM rooms WHERE state='playing'")
            ]

    async def run(self, interval):
        gate = asyncio.Semaphore(8)

        async def reconcile(room_id):
            async with gate:
                try:
                    await self.sync(room_id)
                except DomainError:
                    return

        while True:
            await asyncio.sleep(interval)
            try:
                room_ids = await run_in_threadpool(self._playing_ids)
                await asyncio.gather(*(reconcile(room_id) for room_id in room_ids))
            except self.db.errors:
                logger.warning("Rooms could not scan games for reconciliation")
