# Rooms and Game service boundary

Decision date: 2026-11-17. See ADR-6 and the Assignment 2 delivery plan.

## Ownership

| Owner | Tables/state | Responsibilities |
|---|---|---|
| Rooms | rooms, players, songs, player_songs, demo_catalog; public catalog tables/caches | Admission, credentials, presence, settings, music sources, search, media and snapshot export |
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

## Target cross-service workflows (not yet implemented)

1. **Start:** Rooms authenticates the host, validates revision and readiness, and
   persists a stable start operation together with a frozen snapshot and locked
   admission. It calls Game over HTTP using that operation's identity. Game creates
   at most one game for it. Timeouts have an ambiguous outcome: Rooms retains the
   operation and retries/reconciles by identity instead of allowing another Start
   or reopening the lobby. Explicit rejection can release the reservation only
   after the outcome is established.
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
