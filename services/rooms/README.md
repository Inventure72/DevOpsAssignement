# Rooms service

The standalone Rooms API uses SQL Server 2022. Azure SQL is the deployment target;
the hosted setup has not been verified. Python 3.12+ is required. From the repository root:

```sh
python -m pip install -r services/rooms/requirements.txt
python -m services.rooms
```

Provide `DATABASE_URL` in the process environment before startup. Its format is
`mssql+pymssql://USER:URL_ENCODED_PASSWORD@HOST:1433/DATABASE`. Do not put actual
credentials in source, shell arguments, logs or committed files. `PORT` defaults
to 8001; `GIT_SHA` defaults to `development`. `SERVICE_TOKEN` enables trusted
internal endpoints; there is no default service credential.

Connections require encryption and verify the server hostname and certificate
against the system trust store by default. `?encryption=off` is accepted only for
loopback development connections. Login and statement timeouts are five seconds. Startup retries only
recognized transient connection/Azure-resume errors, at most eight times with
bounded backoff. Authentication/permission errors fail immediately. This setting
has been tested against local SQL Server; Azure cold starts remain unverified.

## Local database setup and real SQL tests

`db/init.sql` creates both service databases and scoped logins idempotently. Execute
it through the bootstrap helper with `SQL_ADMIN_URL`, `ROOMS_DB_PASSWORD` and `GAME_DB_PASSWORD`
provided through the process environment:

```sh
python -m tools.setup_rooms_db
```

The bootstrap creates the login only if absent; it does not rotate an existing
password. Only the setup helper uses the administrator connection. The service
login receives read, write and DDL roles in its own database, without access to a
peer service database. Production Azure resource names/setup are a later step.

To run real SQL Server acceptance with generated temporary credentials and no
manual setup, install `requirements-test.txt` too, start Docker, then run:

```sh
python -m tools.test_rooms_sqlserver
```

The helper starts the prescribed SQL Server 2022 image, bootstraps both scoped logins,
marks both disposable databases, runs the standalone API smoke and SQL acceptance
tests, and removes its test container.
On Apple Silicon it uses the image's amd64 architecture through Docker emulation.
It never connects to Azure or the user's existing application database.

## Migrations, seed data and health

Numbered SQL files in `services/rooms/migrations` run automatically at startup.
A session application lock serializes migration initialization; each migration's
DDL and SHA-256 receipt commit together. Before applying any pending migration,
startup verifies that the receipts match the ordered files and their hashes.
Corrections require a new migration.
The [bundled Demo metadata and audio](../../catalog/bundled_demo/README.md) are
validated at startup. Rooms replaces the shared catalog transactionally while
preserving room-owned song copies. No media download is needed.

`GET /health` reads every required Rooms table and verifies write/read/delete in
the service's own database. Missing tables or failed probes return 503. Startup
also rejects missing migration files or an incomplete schema. Migration log lines
are written to stdout when started with `python -m services.rooms`.

SQL Server uses BIGINT millisecond timestamps, Unicode text and binary identity
collations. Indexed SHA-256 digests preserve unbounded recording/Demo keys without
SQL Server's index-length or trailing-space equality limitations; recording
lookups additionally compare the complete original key in Python. Both composite
membership foreign keys remain. The song-to-membership FK does not cascade, so
room deletion explicitly removes memberships first in the same transaction.
Mutations hold an exclusive application lock. Lobby and snapshot reads take a
shared lock on the same resource before reading, so they cannot deadlock with a
mutation by reading tables in a different order. The health probe does not take
this application lock.

## API and compatibility scope

To start a Demo lobby, post `{"nickname":"Host"}` to `/api/rooms`. A guest
can post `{"nickname":"Guest"}` to `/api/rooms/{room_id}/join`. Both
endpoints return a room-scoped player token and accept optional
`character_id`. Rooms assigns the imported listening facts itself; these
public routes reject client-supplied imports. Resolve a lobby code through
`GET /api/room-codes/{code}`.

Demo previews are served by Rooms. A browser hosted on a different origin
must resolve each relative media path against the Rooms base URL.

Trusted imports and snapshot routes require `X-Service-Token`. Snapshot export
also requires the host's room credential. Public lobby/heartbeat operations use
`Authorization: Bearer <room credential>`. Public lobby output excludes listening
facts and credential hashes. Provider/OAuth/catalog-search orchestration and the
browser's cross-origin transport are not wired to this standalone API yet.

The host starts a game at `POST /api/rooms/{room_id}/start` with their bearer
token and a body such as:

```json
{"request_id":"one-request-id","room_revision":3,"settings":{"round_count":5,"decoys_enabled":false}}
```

Set `GAME_BASE_URL` to the Game origin (HTTPS outside local development) and
configure the same `SERVICE_TOKEN` in both services. Start closes admission
before contacting Game. A `game_start_pending` response means retry the same
request ID, revision and settings. The confirmed reply contains only
`{"game_id":"..."}`; repeating it does not require Game to be reachable.

Use `POST /api/rooms/{room_id}/game/sync` with a room bearer token to retrieve
the current match status and reconcile a recorded Game result. It returns
`{"game":null}` when the room is back in the lobby. The Rooms worker also
reconciles without a client request when Game is configured. If Game is
unavailable, sync returns a retryable 503 and admission remains locked.

Use `POST /api/rooms/{room_id}/leave` with a request ID. Include the game ID
when leaving a match. Host Leave ends that game and preserves the host's room
token. Guests keep their membership when leaving a match; guest Leave without
a game ID in the lobby removes their membership.

The playable monolith still uses SQLite. Explicit
`sqlite:///` URLs remain supported as a compatibility/test mode for Rooms; they
are not the production storage choice. Existing data can be copied with the
[offline transfer tool](../../docs/21_ASSIGNMENT_1_TRANSFER.md).

```sh
bash tools/verify_service.sh rooms
python -m tools.check_service_contracts
```

The normal suite skips the SQL-only acceptance cases. The SQL helper and dedicated
CI job run those cases against a real server. The fixture refuses to initialize or
reset a database without the helper's matching disposable marker.
