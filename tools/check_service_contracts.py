"""Validate committed OpenAPI and reject drift from either independent app."""

import argparse
import json
from pathlib import Path

from openapi_spec_validator import validate

from services.game.app import create_app as game_app
from services.rooms.app import create_app as rooms_app

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="Regenerate both committed files"
    )
    args = parser.parse_args()
    for name, factory in (("rooms", rooms_app), ("game", game_app)):
        path = ROOT / "services" / name / "openapi.json"
        generated = factory().openapi()
        validate(generated)
        if args.write:
            path.write_text(json.dumps(generated, indent=2, sort_keys=True) + "\n")
        elif json.loads(path.read_text()) != generated:
            raise SystemExit(
                f"{name}: OpenAPI drift; run python -m tools.check_service_contracts --write"
            )
        print(f"{name}: OpenAPI specification valid and current")


if __name__ == "__main__":
    main()
