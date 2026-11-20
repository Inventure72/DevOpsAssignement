"""Advance or end a Game using only the facts sent by Rooms over HTTP."""

from shared.contracts import GameLifecycleReport
from shared.errors import DomainError


def abort_interrupted_games(db, game, clock):
    with db.transaction() as conn:
        now = clock()
        for active in game.repo.active(conn):
            game.finish(
                conn, active["id"], max(now, active["started_at_ms"]), "server_restart"
            )


def reconcile_game(conn, game, room_id, payload, now):
    if payload.game_id is not None:
        match = game.repo.game(conn, payload.game_id)
    elif payload.operation_id is not None:
        match = game.repo.start_receipt(conn, room_id, payload.operation_id)
    else:
        match = game.repo.latest(conn, room_id)
    if (
        match is None
        or match["room_id"] != room_id
        or (
            payload.room_revision is not None
            and match["room_revision"] != payload.room_revision
        )
        or (
            payload.operation_id is not None
            and match["start_request_id"] != payload.operation_id
        )
    ):
        raise DomainError(
            "game_not_found", "Game does not match the room operation.", 404
        )
    game_id = match["id"]
    now = max(now, match["started_at_ms"])
    if payload.leave is not None:
        game.require_game(conn, game_id, payload.leave.actor_id, host=True)
    if payload.presence is not None and match["status"] in {"preparing", "playing"}:
        if payload.presence.host_expired:
            game.finish(conn, game_id, now, "host_timeout")
        elif payload.leave is None:
            game.advance(
                conn,
                game_id,
                now,
                payload.presence.host_connected,
                connected_player_ids=set(payload.presence.connected_player_ids),
            )
    if payload.leave is not None:
        game.end(
            conn,
            game_id,
            payload.leave.actor_id,
            {"request_id": payload.leave.request_id},
            now,
            kind="leave",
        )
    result = game.repo.game(conn, game_id)
    return GameLifecycleReport(
        game_id=game_id,
        room_id=room_id,
        room_revision=result["room_revision"],
        operation_id=result["start_request_id"],
        status=result["status"],
        ended_at_ms=result["ended_at_ms"],
        end_reason=result["end_reason"],
    )
