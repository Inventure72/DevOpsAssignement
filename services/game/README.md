# Game service

Run from the repository root with Python 3.12+:

```sh
python -m pip install -r services/game/requirements.txt
python -m services.game
```

Default port: 8002; override with `PORT`. Supply an explicit `DATABASE_URL` in
the environment using `mssql+pymssql://USER:URL_ENCODED_PASSWORD@HOST:1433/DATABASE`.
Use the Game login created by the [local bootstrap](../rooms/README.md#local-database-setup-and-real-sql-tests).
The app applies versioned migrations to its own SQL Server database. It does not
read Rooms tables or import the monolith. Encrypted connections verify the server
certificate and hostname. An explicit `sqlite:///` URL remains supported for
compatibility tests. Rooms sends a persisted Start operation and frozen snapshot
to `POST /internal/games`; Game returns the same Game ID when Rooms retries it.

`GET /health` verifies reads from every required Game table, checks its own
database can write/read/delete and reports `GIT_SHA`. Missing domain tables return
503; startup rejects absent migration files or an incomplete domain schema.
Routes under `/internal/` require `X-Service-Token`, matched
against the process's `SERVICE_TOKEN` environment variable. No token is provided
by default; unconfigured internal endpoints return 503.

Trusted callers can start a game from the validated frozen snapshot, load its
caller-specific projection, prepare clips, advance time-based phases, acknowledge
readiness, submit verified song facts and end it. For the browser, Game exposes
`/api/games/{game_id}` and its audio, preload, ready and answer routes. These
routes use the 60-second bearer issued by Rooms, enforce the frozen roster,
and derive actor identity from the bearer. Only the host can load the audio
manifest and report preload results. The answer route verifies signed
catalog selections for the same room, game and player before scoring.
Set `UI_ORIGIN` to the exact HTTP(S) origin of the static UI.

Timestamps come from Game's clock inside the command lock. The Rooms worker
sends live presence and reconciles terminal results over authenticated HTTP.
Restarting the single Game process aborts interrupted matches while preserving
revealed scores. Rooms discovers the durable result after the restart. The
monolith retains its own browser loop and audio lease fencing.

The domain implementation is shared with the playable monolith by import, rather
than copied. Test from the repository root with verification dependencies installed:

```sh
python -m pytest -q tests/unit/test_game_rules.py tests/unit/test_scoring.py \
  tests/unit/test_selection.py tests/unit/test_round_preparation.py services/game/tests
```

Run `python -m tools.test_rooms_sqlserver` to verify both standalone processes,
mutual database isolation and the Assignment 1 transfer on a disposable SQL
Server 2022 instance. See the [transfer runbook](../../docs/21_ASSIGNMENT_1_TRANSFER.md)
for copying existing data.
