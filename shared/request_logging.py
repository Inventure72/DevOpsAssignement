"""Request diagnostics for the standalone APIs."""

import logging
import sys
from time import perf_counter

from starlette.routing import Match

logger = logging.getLogger(__name__)
METHODS = {
    "GET",
    "HEAD",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
    "TRACE",
    "CONNECT",
}


class RequestLog:
    def __init__(self, app, *, service, routes):
        self.app = app
        self.service = service
        self.routes = routes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = perf_counter()
        root_path = scope.get("root_path", "")
        status = 500

        async def record_status(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, record_status)
        finally:
            duration_ms = (perf_counter() - start) * 1000
            method = scope["method"]
            logger.info(
                "service=%s method=%s path=%s status=%d duration_ms=%.2f",
                self.service,
                method if method in METHODS else "OTHER",
                self._path(scope, root_path),
                status,
                duration_ms,
            )

    def _path(self, scope, root_path):
        route = scope.get("route")
        if route is None:
            # Mounts and CORS preflights may not populate the resolved route.
            match_scope = {**scope, "root_path": root_path}
            route = next(
                (r for r in self.routes if r.matches(match_scope)[0] != Match.NONE),
                None,
            )
        return getattr(route, "path_format", "<unmatched>")


class _SafeRequestErrors(logging.Filter):
    def filter(self, record):
        # Uvicorn's request traceback can contain exception text from user input.
        if record.msg == "Exception in ASGI application\n":
            record.msg = "Unhandled ASGI request error"
            record.args = ()
            record.exc_info = record.exc_text = record.stack_info = None
        return True


def configure_logging():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").addFilter(_SafeRequestErrors())
