"""Rooms-to-Game HTTP transport for Start."""

import asyncio
from urllib.parse import urlsplit

import httpx

from shared.contracts import StartedGame
from shared.errors import DomainError


def start_pending():
    return DomainError(
        "game_start_pending",
        "Start is pending. Retry with the same request ID and settings.",
        503,
    )


class GameClient:
    def __init__(self, base_url, service_token, *, timeout=3.0, transport=None):
        try:
            url = urlsplit(base_url)
            if (
                not url.hostname
                or url.username is not None
                or url.password is not None
                or url.query
                or url.fragment
                or url.path not in ("", "/")
                or url.port == 0
                or (
                    url.scheme != "https"
                    and not (
                        url.scheme == "http"
                        and url.hostname in {"localhost", "127.0.0.1", "::1", "game"}
                    )
                )
            ):
                raise ValueError
        except ValueError:
            raise ValueError(
                "GAME_BASE_URL must be an HTTPS origin or a local Game HTTP origin"
            ) from None
        self._url = base_url.rstrip("/") + "/internal/games"
        self._service_token = service_token
        self._timeout = timeout
        self._transport = transport

    async def start(self, payload):
        try:
            async with asyncio.timeout(self._timeout):
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    follow_redirects=False,
                    trust_env=False,
                    transport=self._transport,
                ) as client:
                    response = await client.post(
                        self._url,
                        headers={"X-Service-Token": self._service_token},
                        json=payload.model_dump(mode="json"),
                    )
                    if response.status_code != 200:
                        raise start_pending()
                    return StartedGame.model_validate_json(response.content)
        except (TimeoutError, httpx.HTTPError, ValueError):
            raise start_pending() from None
