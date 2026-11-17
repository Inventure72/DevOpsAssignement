# Game extraction checkpoint

Run from the repository root with Python 3.12+:

```sh
python -m pip install -r services/game/requirements.txt
python -m services.game
```

Default port: 8002; override with `PORT`. The app initializes only Game tables in
`data/game.sqlite3`. `DATABASE_URL` accepts an explicit `sqlite:///` development
path. SQL Server and the HTTP start/reservation workflow are pending milestones.
The app does not import Rooms or the monolith, or connect to their databases.

`GET /health` verifies its own database can read/write and reports `GIT_SHA`.
All gameplay routes are under `/internal/` and require `X-Service-Token`, matched
against the process's `SERVICE_TOKEN` environment variable. No token is provided
by default; unconfigured internal endpoints return 503.

Trusted callers can start a game from the validated frozen snapshot, load its
caller-specific projection, prepare clips, advance time-based phases, acknowledge
readiness, submit verified song facts and end it. Caller IDs and song facts are
trusted internal input, not public browser authentication. Production orchestration
must authenticate the room session and verify signed catalog selections before
using these operations. Private audio manifests remain host-only.

Timestamps come from the service clock inside the command lock. The extraction
API has explicit phase advancement; the monolith still owns the complete browser
loop, audio lease fencing, due-before-command behavior and restart reconciliation.
Do not switch the UI to these routes until that orchestration is ported and tested.

The domain implementation is shared with the playable monolith by import, rather
than copied. Test from the repository root with verification dependencies installed:

```sh
python -m pytest -q tests/unit/test_game_rules.py tests/unit/test_scoring.py \
  tests/unit/test_selection.py tests/unit/test_round_preparation.py services/game/tests
```
