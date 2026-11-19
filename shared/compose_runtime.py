"""Load the scoped local Compose credentials before starting a service."""

import json
import os
import sys
from pathlib import Path


def service_environment(owner):
    if owner not in {"rooms", "game"}:
        raise ValueError("Expected rooms or game")
    directory = Path("/run/repeat") / owner
    values = json.loads((directory / "connection.json").read_text())
    return {
        "DATABASE_URL": values["DATABASE_URL"],
        "SERVICE_TOKEN": values["SERVICE_TOKEN"],
        "SSL_CERT_FILE": str(directory / "server.pem"),
    }


if __name__ == "__main__":
    owner = sys.argv[1]
    os.environ.update(service_environment(owner))
    os.execv(sys.executable, [sys.executable, "-m", f"services.{owner}"])
