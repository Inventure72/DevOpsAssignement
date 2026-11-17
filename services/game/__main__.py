"""Start Game alone from the repository root: python -m services.game."""

import os

import uvicorn


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8002"))
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    uvicorn.run("services.game.app:app", host="0.0.0.0", port=port, workers=1)
