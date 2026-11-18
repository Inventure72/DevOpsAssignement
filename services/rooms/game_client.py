"""Rooms-to-Game HTTP transport for Start."""

import asyncio
from urllib.parse import urlsplit
from urllib.parse import quote

import httpx

from shared.contracts import GameLifecycleReport, StartedGame
from shared.errors import DomainError


def start_pending():
    return DomainError(
        "game_start_pending",
        "Start is pending. Retry with the same request ID and settings.",
        503,
    )


def lifecycle_unavailable():
    return DomainError(
        "game_lifecycle_unavailable",
        "The game could not be synchronized. Retry the same action.",
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
        self._origin = base_url.rstrip("/")
        self._service_token = service_token
        self._timeout = timeout
        self._transport = transport

    async def start(self, payload):
        return await self._post("/internal/games", payload, StartedGame, start_pending)

    async def reconcile(self, room_id, payload):
        report = await self._post(
            f"/internal/rooms/{quote(room_id, safe='')}/game-lifecycle",
            payload,
            GameLifecycleReport,
            lifecycle_unavailable,
        )
        if (
            report.room_id != room_id
            or (payload.game_id is not None and report.game_id != payload.game_id)
            or (
                payload.operation_id is not None
                and report.operation_id != payload.operation_id
            )
            or (
                payload.room_revision is not None
                and report.room_revision != payload.room_revision
            )
        ):
            raise lifecycle_unavailable()
        return report

    async def _post(self, path, payload, model, unavailable):
        try:
            async with asyncio.timeout(self._timeout):
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    follow_redirects=False,
                    trust_env=False,
                    transport=self._transport,
                ) as client:
                    response = await client.post(
                        self._origin + path,
                        headers={"X-Service-Token": self._service_token},
                        json=payload.model_dump(mode="json"),
                    )
                    if response.status_code != 200:
                        raise unavailable()
                    return model.model_validate_json(response.content)
        except (TimeoutError, httpx.HTTPError, ValueError):
            raise unavailable() from None
