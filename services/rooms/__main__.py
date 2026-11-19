"""Start Rooms alone from the repository root: python -m services.rooms."""

import os

import uvicorn

from shared.request_logging import configure_logging


if __name__ == "__main__":
    configure_logging()
    port = int(os.environ.get("PORT", "8001"))
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    uvicorn.run(
        "services.rooms.app:app", host="0.0.0.0", port=port, workers=1, access_log=False
    )
