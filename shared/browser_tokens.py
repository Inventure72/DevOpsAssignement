"""Short-lived, purpose-specific credentials issued by Rooms for Game."""

import base64
import hashlib
import hmac
from typing import TypeVar

from shared.contracts import Body, GameID, Identifier, Song
from shared.errors import DomainError

GAME_SESSION_SECONDS = 60
SELECTION_LIFETIME_MS = 20 * 60_000
MAX_BROWSER_TOKEN_LENGTH = 8192
Claims = TypeVar("Claims", bound=Body)


class GameSession(Body):
    room_id: Identifier
    game_id: GameID
    player_id: Identifier
    expires_at_ms: int


class SongSelection(GameSession):
    song: Song


class BrowserTokens:
    def __init__(self, service_token: str):
        self.secret = service_token.encode()

    def _authenticate(self, schema, body):
        if not self.secret:
            raise DomainError(
                "service_unconfigured", "Game access is not configured.", 503
            )
        purpose = b"session" if schema is GameSession else b"selection"
        key = hmac.digest(self.secret, b"repeat/browser/v1/" + purpose, "sha256")
        return hmac.new(key, body.encode("ascii"), hashlib.sha256).hexdigest()

    def issue(self, value: GameSession) -> str:
        body = base64.urlsafe_b64encode(value.model_dump_json().encode())
        encoded = body.decode().rstrip("=")
        return encoded + "." + self._authenticate(type(value), encoded)

    def decode(self, token: str, schema: type[Claims]) -> Claims:
        if len(token) <= MAX_BROWSER_TOKEN_LENGTH:
            try:
                body, signature = token.split(".")
                if hmac.compare_digest(
                    signature.encode(), self._authenticate(schema, body).encode()
                ):
                    decoded = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
                    return schema.model_validate_json(decoded)
            except ValueError:
                pass
        if schema is GameSession:
            raise DomainError("invalid_game_session", "Reconnect to this game.", 401)
        raise DomainError("invalid_song_selection", "Choose a song from search.")
