"""Game-owned private API over validated frozen values and isolated storage."""

import time
from pathlib import Path
from threading import RLock
from typing import Annotated, Literal

from fastapi import Depends
from pydantic import Field

from services.game.domain.service import GameService
from services.game.domain.views import audio_manifest, game_view
from shared.contracts import Body, Identifier, RoomSnapshot, Song
from shared.development import ServiceConfig, service_app


class Settings(Body):
    round_count: Literal[5, 10, 15] = 10
    answer_seconds: Literal[10, 20, 30] = 20
    difficulty: Literal["easy", "mixed", "hard"] = "mixed"
    decoys_enabled: bool = True


class StartCommand(Body):
    request_id: Identifier
    room_revision: Annotated[int, Field(ge=0)]


class StartGame(Body):
    snapshot: RoomSnapshot
    settings: Settings
    actor_id: Identifier
    command: StartCommand


class Actor(Body):
    actor_id: Identifier


class Command(Actor):
    request_id: Identifier


class Preload(Command):
    candidate_id: Identifier
    ok: bool
    waveform: (
        Annotated[
            list[Annotated[float, Field(ge=0, le=1)]],
            Field(min_length=8, max_length=64),
        ]
        | None
    ) = None


class Presence(Actor):
    host_connected: bool
    connected_player_ids: Annotated[list[str], Field(max_length=10)]


class Ready(Actor):
    readiness_generation: Annotated[int, Field(ge=1)]


class Answer(Actor):
    song_guess: Song | None = None
    who_player_ids: Annotated[list[str], Field(max_length=10)] = []


def create_app(config=None, *, clock=None, game=None):
    config = config or ServiceConfig.from_env("game")
    app, db, internal = service_app(
        "Game",
        config,
        Path(__file__).parent / "development",
        required_tables=(
            "games",
            "game_players",
            "rounds",
            "answers",
            "game_commands",
            "game_round_preparations",
        ),
    )
    game = game or GameService()
    clock = clock or (lambda: time.time_ns() // 1_000_000)
    lock = RLock()

    @app.post("/internal/games", dependencies=[Depends(internal)])
    def start(payload: StartGame) -> dict:
        with lock, db.transaction() as conn:
            return game.start(
                conn,
                payload.snapshot.model_dump(),
                payload.settings.model_dump(),
                payload.actor_id,
                payload.command.model_dump(),
                clock(),
            )

    @app.get("/internal/games/{game_id}", dependencies=[Depends(internal)])
    def state(game_id: str, actor_id: str) -> dict:
        with db.read() as conn:
            value = game.require_game(conn, game_id, actor_id)
            host = next(
                p["is_host"]
                for p in game.repo.roster(conn, game_id)
                if p["player_id"] == actor_id
            )
            return game_view(game.repo, conn, value, actor_id, bool(host))

    @app.get("/internal/games/{game_id}/audio", dependencies=[Depends(internal)])
    def audio(game_id: str, actor_id: str) -> dict:
        with db.read() as conn:
            value = game.require_game(conn, game_id, actor_id, host=True)
            return audio_manifest(value)

    @app.post("/internal/games/{game_id}/preload", dependencies=[Depends(internal)])
    def preload(game_id: str, payload: Preload) -> dict:
        with lock, db.transaction() as conn:
            return game.preload(
                conn,
                game_id,
                payload.actor_id,
                payload.model_dump(exclude={"actor_id"}),
                clock(),
            )

    @app.post("/internal/games/{game_id}/advance", dependencies=[Depends(internal)])
    def advance(game_id: str, payload: Presence) -> dict:
        with lock, db.transaction() as conn:
            game.require_game(conn, game_id, payload.actor_id)
            game.advance(
                conn,
                game_id,
                clock(),
                payload.host_connected,
                connected_player_ids=set(payload.connected_player_ids),
            )
            return {"accepted": True}

    @app.post(
        "/internal/games/{game_id}/rounds/{round_id}/ready",
        dependencies=[Depends(internal)],
    )
    def ready(game_id: str, round_id: str, payload: Ready) -> dict:
        with lock, db.transaction() as conn:
            return game.ready(
                conn,
                game_id,
                round_id,
                payload.actor_id,
                payload.readiness_generation,
                clock(),
            )

    @app.post(
        "/internal/games/{game_id}/rounds/{round_id}/answers",
        dependencies=[Depends(internal)],
    )
    def answer(game_id: str, round_id: str, payload: Answer) -> dict:
        with lock, db.transaction() as conn:
            return game.answer(
                conn,
                game_id,
                round_id,
                payload.actor_id,
                payload.model_dump(exclude={"actor_id"}),
                clock(),
            )

    @app.post("/internal/games/{game_id}/end", dependencies=[Depends(internal)])
    def end(game_id: str, payload: Command) -> dict:
        with lock, db.transaction() as conn:
            return game.end(
                conn,
                game_id,
                payload.actor_id,
                payload.model_dump(exclude={"actor_id"}),
                clock(),
            )

    return app


app = create_app()
