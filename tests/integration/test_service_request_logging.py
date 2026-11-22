"""Exercise status handling for requests that bypass normal API handlers."""

import logging
import sys

from fastapi.testclient import TestClient

from services.rooms.app import create_app
from services.rooms.bundled_demo import CLIPS, MEDIA_PATH
from shared.development import ServiceConfig
from shared.request_logging import _SafeRequestErrors


def test_mount_preflight_and_failed_handler_use_safe_paths(tmp_path, caplog):
    origin = "https://ui.test"
    app = create_app(
        ServiceConfig(f"sqlite:///{tmp_path}/rooms.sqlite3", ui_origin=origin),
        lifecycle_interval=None,
    )

    @app.get("/failed/{test_value}")
    def failed(test_value: str):
        raise RuntimeError(test_value)

    with TestClient(app, raise_server_exceptions=False) as client:
        caplog.set_level(logging.INFO, logger="shared.request_logging")
        preflight = client.options(
            "/api/rooms/private-room-id/lobby",
            headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == origin
        assert (
            client.head(f"{MEDIA_PATH}/{next(CLIPS.glob('*.mp3')).name}").status_code
            == 200
        )
        assert (
            client.get("/failed/private-error-data?secret=some-query").status_code
            == 500
        )
        assert client.request("PRIVATE-METHOD", "/health").status_code == 405

    records = [
        record.getMessage()
        for record in caplog.records
        if record.name == "shared.request_logging"
    ]
    assert [line.split(" duration_ms=")[0] for line in records] == [
        "service=rooms method=OPTIONS path=/api/rooms/{room_id}/lobby status=200",
        f"service=rooms method=HEAD path={MEDIA_PATH}/{{path}} status=200",
        "service=rooms method=GET path=/failed/{test_value} status=500",
        "service=rooms method=OTHER path=/health status=405",
    ]


def test_uvicorn_request_error_omits_exception_text():
    try:
        raise RuntimeError("private exception details")
    except RuntimeError:
        record = logging.LogRecord(
            "uvicorn.error",
            logging.ERROR,
            __file__,
            1,
            "Exception in ASGI application\n",
            (),
            sys.exc_info(),
        )
    assert _SafeRequestErrors().filter(record)
    assert logging.Formatter().format(record) == "Unhandled ASGI request error"
