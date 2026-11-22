#!/usr/bin/env bash
# Isolated core coverage and own-database API checks for one extracted service.
set -euo pipefail
verification_repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$verification_repo"
verification_python="${PYTHON:-.venv/bin/python}"
case "${1:-}" in
  rooms)
    export COVERAGE_FILE="${COVERAGE_FILE:-.coverage.rooms}"
    "$verification_python" -m pytest -q tests/unit/test_rooms_rules.py services/rooms/tests \
      --cov=services.rooms.domain.service --cov-report=term-missing --cov-fail-under=90
    ;;
  game)
    export COVERAGE_FILE="${COVERAGE_FILE:-.coverage.game}"
    "$verification_python" -m pytest -q tests/unit/test_game_rules.py \
      tests/unit/test_scoring.py tests/unit/test_selection.py \
      tests/unit/test_round_preparation.py services/game/tests \
      --cov=services.game.domain.service --cov=services.game.domain.scoring \
      --cov=services.game.domain.selection --cov=services.game.domain.preparation \
      --cov=services.game.domain.song_titles --cov-report=term-missing --cov-fail-under=90
    ;;
  *) printf 'Usage: bash tools/verify_service.sh rooms|game\n' >&2; exit 2 ;;
esac
