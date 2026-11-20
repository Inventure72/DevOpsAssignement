#!/usr/bin/env bash
# Run unit coverage, integration and frontend checks.
set -euo pipefail
verification_repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$verification_repo"
verification_python="${PYTHON:-.venv/bin/python}"
"$verification_python" -m pytest -q tests/unit \
  --cov=services.rooms.domain.service --cov=services.game.domain.service \
  --cov=services.game.domain.scoring --cov=services.game.domain.selection \
  --cov=services.game.domain.preparation --cov=services.game.domain.song_titles \
  --cov-report=term-missing --cov-fail-under=90
# Enforce the target in each domain; a stronger domain cannot hide a weaker one.
"$verification_python" -m coverage report \
  --include=services/rooms/domain/service.py --fail-under=90
"$verification_python" -m coverage report \
  --include='services/game/domain/service.py,services/game/domain/scoring.py,services/game/domain/selection.py,services/game/domain/preparation.py,services/game/domain/song_titles.py' \
  --fail-under=90
# Preparation is small and critical: also prevent dilution within Game coverage.
"$verification_python" -m coverage report \
  --include=services/game/domain/preparation.py --fail-under=90
"$verification_python" -m pytest -q tests/integration services/rooms/tests services/game/tests
node --test tests/frontend/*.test.mjs
