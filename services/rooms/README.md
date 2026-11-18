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

`db/init.sql` creates `repeat_rooms` and `repeat_rooms_user` idempotently. Execute
it through the bootstrap helper with `SQL_ADMIN_URL` and `ROOMS_DB_PASSWORD`
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

The helper starts the prescribed SQL Server 2022 image, bootstraps a scoped login,
marks the disposable database, runs the standalone API smoke and SQL acceptance
tests, and removes its test container.
On Apple Silicon it uses the image's amd64 architecture through Docker emulation.
It never connects to Azure or the user's existing application database.

## Migrations, seed data and health

Numbered SQL files in `services/rooms/migrations` run automatically at startup.
A session application lock serializes migration initialization; each migration's
DDL and SHA-256 receipt commit together. Before applying any pending migration,
startup verifies that the receipts match the ordered files and their hashes.
Corrections require a new migration.
The Demo metadata catalog is validated and replaced transactionally from the
committed `catalog/demo_catalog.json`; room-owned song copies are preserved.
No media download is performed by this API.

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

Trusted imports and snapshot routes require `X-Service-Token`. Snapshot export
also requires the host's room credential. Public lobby/heartbeat operations use
`Authorization: Bearer <room credential>`. Public lobby output excludes listening
facts and credential hashes. Provider/OAuth/catalog-search orchestration and the
browser's cross-origin transport are not wired to this standalone API yet.

The playable monolith and Game extraction still use SQLite. Explicit
`sqlite:///` URLs remain supported as a compatibility/test mode for Rooms; they
are not the production storage choice. Existing SQLite data transfer is scheduled
with the Game/database split work and does not happen automatically here.

```sh
bash tools/verify_service.sh rooms
python -m tools.check_service_contracts
```

The normal suite skips the SQL-only acceptance cases. The SQL helper and dedicated
CI job run those cases against a real server. The fixture refuses to initialize or
reset a database without the helper's matching disposable marker.
