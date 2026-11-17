# Rooms extraction checkpoint

Run from the repository root with Python 3.12+:

```sh
python -m pip install -r services/rooms/requirements.txt
python -m services.rooms
```

Default port: 8001. Set `PORT` to override. The app creates only Rooms tables in
`data/rooms.sqlite3`. Set `DATABASE_URL=sqlite:////absolute/path/rooms.sqlite3` to
use another development database. This is a temporary SQLite runtime; the planned
SQL Server port is not implemented yet. It never opens the monolith database.

`GET /health` checks an actual write/read/delete in this database and reports
`GIT_SHA` (default `development`). It returns 503 if its own database is unavailable.

Set `SERVICE_TOKEN` through the process environment to enable trusted internal
admission and snapshot routes. There is no default credential; these routes return
503 when it is absent and 401 for an invalid `X-Service-Token` header. Admission
accepts a validated verified-import DTO. Provider/OAuth orchestration and browser
admission remain in the existing monolith until the next integration milestone.

Public lobby/heartbeat operations use `Authorization: Bearer <room credential>`.
Internal snapshot export requires both the service header and the host's room
credential. Public lobby output excludes listening facts and credential hashes.
The standalone API is not yet wired to the existing browser's cookie transport.

Tests run from the repository root with the root verification dependencies:

```sh
python -m pytest -q tests/unit/test_rooms_rules.py services/rooms/tests
```

The monolith remains playable with its existing launchers and imports this same
domain implementation. See `docs/20_SERVICE_SPLIT.md` for the target HTTP lifecycle
and outstanding migration work.
