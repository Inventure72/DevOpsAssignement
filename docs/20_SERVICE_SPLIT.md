# Rooms and Game service boundary

Decision date: 2026-11-17. See ADR-6 and the Assignment 2 delivery plan.

## Ownership

| Owner | Tables/state | Responsibilities |
|---|---|---|
| Rooms | rooms, players, songs, player_songs, demo_catalog, room_start_operations; public catalog tables/caches | Admission, credentials, presence, settings, music sources, search, media and Start reservations |
| Game | games, game_players, rounds, answers, game_commands, game_round_preparations; audio leases | Frozen facts, candidate planning, readiness, scoring, rankings and playback authority |

`games.room_id` becomes a reference value, without a cross-service foreign key.
`game_players.player_id` is a frozen identity. Game results must survive later
changes to lobby names, songs and membership. Each service keeps its own internal
foreign keys. Room expiry must be coordinated without SQL joins across databases.

## Value contracts and authorization

The current `RoomsService.snapshot` exports room ID/revision/mode, host ID,
minimum players, roster values and playable songs with listener familiarity.
It includes private facts and must never be returned by a public lobby endpoint.
The receiving Game service freezes these values; it does not query Rooms for
historical listening facts. Transport validation must reject malformed input
before entering domain code.

Standalone extraction APIs use a configured service credential on all internal
routes. Imported listening facts are accepted only through this trusted boundary,
not directly from an anonymous browser. Exporting a snapshot also authenticates
the host's room credential. Health, public room-code lookup and authenticated
lobby views do not return credentials, private song pools or snapshots.

The existing cookie-based browser contract remains in the monolith during this
checkpoint. The split browser authentication contract must be implemented and
tested before switching the UI to separate HTTPS origins.

## Cross-service workflows

1. **Start:** Rooms authenticates the host, validates revision and readiness, and
   persists a stable start operation together with a frozen snapshot and locked
   admission. It calls Game over HTTP using that operation's identity. Game creates
   at most one game for it. Timeouts have an ambiguous outcome: Rooms retains the
   operation and retries by identity instead of allowing another Start or
   reopening the lobby. Local validation failures never reserve a room.
2. **Presence and authorization:** Game receives current authority/presence over
   authenticated HTTP rather than reading live Rooms tables. Frozen listener
   ownership remains unchanged. Each outbound request has a bounded timeout.
3. **Completion/abort:** the durable Game result reconciles Rooms back to lobby
   through an idempotent HTTP operation. An interrupted notification remains
   retryable without changing revealed scores or applying completion twice.
4. **Search:** Rooms owns public catalog lookup and verifies/signs song facts.
   Game accepts only authenticated verified selections; moving the signer must
   preserve the existing room binding and expiry checks.

No distributed transaction or message broker is introduced. Pending operations
and their reconciliation policy must be persisted before the monolith coordinator
is retired. Domain-owned reads remain usable when the peer is unavailable; a
cross-service operation reports a bounded retryable error.

## Extraction checkpoint and remaining work

Today's deliverables physically relocate both domains, provide independent app
entry points and dependency manifests, commit generated OpenAPI documents, and
test each against its own development schema. Pure shared helpers hold no domain
state. The monolith imports the relocated implementation rather than duplicating
it, preserving current gameplay and regression coverage.

Development SQLite databases are disposable test/development resources, separate
from the existing monolith database. Their initial schemas contain only the owning
domain's tables. They are not the Assignment 2 SQL Server deliverable and do not
transfer existing records automatically. SQL Server, data transfer, provider/UI
wiring, durable cross-service orchestration, deployment and production migration
evidence remain scheduled work.

## Verified checkpoint — 2026-11-17

Local verification of a clean copy of the staged deliverables, installed into a
fresh project `.venv`, passed **323 public-source unit tests, 256 integration/API/
process tests and 214 frontend tests**. Core coverage remains **94.79%** overall:
Rooms **94.38%**, Game **94.94%**. Independent service checks passed 35 Rooms and
91 Game checks; these overlap the full suite and are not additional unique tests.

The real-process test starts both entry points with isolated temporary databases,
creates memberships, exports a host-authorized snapshot, starts Game through its
private API and observes successful Game health/frozen reads after Rooms stops.
The test harness transfers the snapshot; server-side Start orchestration is still
pending. No live provider, physical-device audio or Azure deployment is claimed.

OpenAPI validation/drift checks, application-source lint and `actionlint` passed.
The clean-source check found that existing launcher tests require a project-local
`.venv`; the regression CI job now creates it before installation. The corrected
clean-source verification passed without weakening those tests. GitHub Actions
execution remains unverified until the branch is pushed.

Review follow-up (2026-11-17): a temporary-database reproduction exposed a false
healthy response after removing a required domain table, and startup accepted an
empty migration directory. Startup now requires migration files and an intact
own-domain schema; health reads every required domain table before its write/read/
delete probe. Thirteen new regression cases cover both services' tables and
missing/empty migration directories. Full public-source verification after the
fix passed **323 unit, 269 integration/API/process and 214 frontend tests**, with
unchanged **94.79%** core coverage. Source lint and OpenAPI checks also passed.

## Rooms SQL Server milestone — 2026-11-18

The standalone Rooms API now uses SQL Server when given a `mssql+pymssql`
DATABASE_URL; startup requires an explicit URL. An explicit SQLite URL remains
a compatibility/test path, and the playable monolith is unchanged. Game storage,
existing SQLite data transfer, catalog-search/provider orchestration and the
cross-service lifecycle remain separate integration milestones.

Rooms migrations and their checksummed receipts commit atomically, with a session
lock around initialization. Demo seeding is transactional and preserves room song
copies. Runtime writes use a transaction-owned lock across independent connections.
The local bootstrap grants the service login roles only in its own database; the
service does not use the administrator connection. No Azure resource was changed.

SQL Server's multiple-cascade-path restriction requires one membership FK to use
NO ACTION; room deletion explicitly removes memberships inside its transaction.
Exact identity checks and indexed SHA-256 digests avoid trailing-space comparison
and index-width problems while keeping complete original recording keys. Health
checks retain the missing-schema and own-database read/write guarantees.

## Game SQL Server and data transfer milestone — 2026-11-18

Game now applies its own versioned SQL Server migration with a separate login,
keeps its frozen roster and listening facts, and checks only its own database.
Both standalone APIs support explicit SQLite URLs for compatibility tests. The
existing monolith remains available while HTTP coordination is implemented.

The [offline transfer](21_ASSIGNMENT_1_TRANSFER.md) copies the final Assignment 1
SQLite schema to the two initialized databases and verifies both copies. It uses
one receipt per target to recover after an interrupted second commit. No real
project data or Azure databases are needed for the SQL Server tests.

## HTTP Start milestone — 2026-11-18

1. The host sends their room bearer token, client request ID, lobby revision and
   settings to Rooms. Rooms validates authorization, revision and readiness.
2. The Rooms transaction saves a new operation ID and private Game payload in
   `room_start_operations` and moves the room to `playing`, closing admission.
   A row with no Game ID is an unresolved Start. No database lock spans HTTP.
3. Rooms posts the saved values to Game over authenticated HTTP, with a
   three-second outbound deadline. Game commits one game per room and operation ID
   and returns that Game ID on repeat. A second Rooms transaction stores the result.
4. An unavailable peer, non-success or invalid reply, or failure saving the response
   yields a retryable 503. The host resends the original client request, including
   its settings and revision. Rooms dispatches the original frozen facts or
   returns the confirmed Game ID locally. A new request ID cannot start another
   game while the room is locked. No timer reopens an unresolved lobby.

Game sees the shared service token but never the host's room token. The private
snapshot is absent from public replies. Both APIs keep their own health checks.
Real-process acceptance starts a game through Rooms without forwarding the
snapshot in the test harness, on SQLite and SQL Server. Recovery tests use the
real apps with an injected HTTP transport fault after Game commits; separate
tests stub peer responses for deadline and Rooms confirmation failures. No
Azure deployment or browser cutover is claimed. Completion, presence and
lifecycle reconciliation remain the next milestone.
