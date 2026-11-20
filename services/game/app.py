"""Game API over validated frozen values and isolated storage."""

import time
from pathlib import Path
from threading import RLock
from typing import Annotated

from fastapi import Depends, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field

from services.game.domain.repository import SqlServerGameRepository
from services.game.domain.service import GameService
from services.game.domain.views import audio_manifest, game_view
from services.game.lifecycle import abort_interrupted_games, reconcile_game
from services.game.storage import REQUIRED_TABLES, GameDatabase
from shared.browser_tokens import (
    MAX_BROWSER_TOKEN_LENGTH,
    BrowserTokens,
    GameSession,
    SongSelection,
)
from shared.contracts import (
    Body,
    GameID,
    GameLifecycleReport,
    Identifier,
    ReconcileGame,
    Song,
    StartedGame,
    StartGame,
)
from shared.development import ServiceConfig, service_app
from shared.errors import DomainError


class Actor(Body):
    actor_id: Identifier


class Command(Actor):
    request_id: Identifier


class BrowserPreload(Body):
    request_id: Identifier
    candidate_id: Identifier
    ok: bool
    waveform: (
        Annotated[
            list[Annotated[float, Field(ge=0, le=1)]],
            Field(min_length=8, max_length=64),
        ]
        | None
    ) = None


class Preload(BrowserPreload, Actor):
    pass


class Presence(Actor):
    host_connected: bool
    connected_player_ids: Annotated[list[str], Field(max_length=10)]


class BrowserReady(Body):
    readiness_generation: Annotated[int, Field(ge=1)]


class Ready(BrowserReady, Actor):
    pass


class Answer(Actor):
    song_guess: Song | None = None
    who_player_ids: Annotated[list[str], Field(max_length=10)] = []


class BrowserAnswer(Body):
    song_selection: Annotated[
        str | None, Field(max_length=MAX_BROWSER_TOKEN_LENGTH)
    ] = None
    who_player_ids: Annotated[list[Identifier], Field(max_length=10)] = []


def create_app(config=None, *, clock=None, game=None):
    config = config or ServiceConfig.from_env("game", default_database_url="")
    database = (
        None
        if config.database_url.startswith("sqlite:///")
        else GameDatabase(config.database_url)
    )
    game = game or GameService(
        repository=SqlServerGameRepository() if database is not None else None
    )
    clock = clock or (lambda: time.time_ns() // 1_000_000)
    app, db, internal = service_app(
        "Game",
        config,
        Path(__file__).parent / "development",
        required_tables=REQUIRED_TABLES,
        database=database,
        after_initialize=lambda db: abort_interrupted_games(db, game, clock),
    )
    lock = RLock()
    bearer = HTTPBearer(auto_error=False)
    browser_tokens = BrowserTokens(config.service_token)

    def game_session(
        game_id: GameID, auth: HTTPAuthorizationCredentials | None = Depends(bearer)
    ) -> GameSession:
        if auth is None:
            raise DomainError("invalid_game_session", "Reconnect to this game.", 401)
        session = browser_tokens.decode(auth.credentials, GameSession)
        if session.game_id != game_id or session.expires_at_ms <= clock():
            raise DomainError("invalid_game_session", "Reconnect to this game.", 401)
        return session

    @app.post(
        "/internal/games", dependencies=[Depends(internal)], response_model=StartedGame
    )
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

    @app.post(
        "/internal/rooms/{room_id}/game-lifecycle", dependencies=[Depends(internal)]
    )
    def lifecycle(room_id: str, payload: ReconcileGame) -> GameLifecycleReport:
        with lock, db.transaction() as conn:
            return reconcile_game(conn, game, room_id, payload, clock())

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
                host_connected=False,
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

    @app.get("/api/games/{game_id}")
    def browser_state(
        game_id: GameID,
        response: Response,
        session: GameSession = Depends(game_session),
    ) -> dict:
        response.headers["Cache-Control"] = "no-store"
        return {**state(game_id, session.player_id), "server_now_ms": clock()}

    @app.get("/api/games/{game_id}/audio")
    def browser_audio(
        game_id: GameID,
        response: Response,
        session: GameSession = Depends(game_session),
    ) -> dict:
        response.headers["Cache-Control"] = "no-store"
        return audio(game_id, session.player_id)

    @app.post("/api/games/{game_id}/preload")
    def browser_preload(
        game_id: GameID,
        payload: BrowserPreload,
        session: GameSession = Depends(game_session),
    ) -> dict:
        return preload(
            game_id, Preload(actor_id=session.player_id, **payload.model_dump())
        )

    @app.post("/api/games/{game_id}/rounds/{round_id}/ready")
    def browser_ready(
        game_id: GameID,
        round_id: GameID,
        payload: BrowserReady,
        session: GameSession = Depends(game_session),
    ) -> dict:
        return ready(
            game_id,
            round_id,
            Ready(
                actor_id=session.player_id,
                readiness_generation=payload.readiness_generation,
            ),
        )

    @app.post("/api/games/{game_id}/rounds/{round_id}/answers")
    def browser_answer(
        game_id: GameID,
        round_id: GameID,
        payload: BrowserAnswer,
        session: GameSession = Depends(game_session),
    ) -> dict:
        answer = {"song_guess": None, "who_player_ids": payload.who_player_ids}
        if payload.song_selection is not None:
            choice = browser_tokens.decode(payload.song_selection, SongSelection)
            if (choice.room_id, choice.game_id, choice.player_id) != (
                session.room_id,
                game_id,
                session.player_id,
            ):
                raise DomainError(
                    "invalid_song_selection", "Choose a song from search."
                )
            answer["song_guess"] = choice.song.model_dump()
            answer["_token_expires_ms"] = choice.expires_at_ms
        with lock, db.transaction() as conn:
            if (
                conn.execute(
                    "SELECT id FROM rounds WHERE game_id=? AND id=?",
                    (game_id, round_id),
                ).fetchone()
                is None
            ):
                raise DomainError(
                    "stale_attempt", "This round attempt is no longer current.", 409
                )
            return game.answer(
                conn, game_id, round_id, session.player_id, answer, clock()
            )

    return app


app = create_app()
