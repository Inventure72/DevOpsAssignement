# Architecture Decision Records

Assignment 1 decisions 1–5 retain their original dates and text. Assignment 2
decisions begin at 6; intended changes are distinguished from implemented work.
First commits: ADR-1 `7fd5e42` (Sep 27), ADR-2 `e5b091b` (Sep 29), ADR-3 `acc5000` (Sep 30), ADR-4/5 `a55de5a` (Oct 1).

## 1. Backend framework: Python + FastAPI

Date: 2026-09-25

Status: Decided

Context: The app needs one process to serve the browser, validate commands and coordinate music imports. Python is a stack I can study and explain.

Decision: Use FastAPI, one Uvicorn worker, standard-library SQLite and one root `requirements.txt`.

Alternatives considered: Flask is lighter; FastAPI provides typed validation and lifespan management. Django adds an ORM, admin and account system beyond this game's scope.

Consequences: HTTP validation stays at the API boundary. Synchronous SQLite work runs outside the event loop, while lifespan owns initialization and the phase task; transaction and task boundaries need careful coordination.

Frontend rationale (2026-10-04): native JavaScript modules and CSS are served by the same process. Server templates still need browser code for polling, readiness and host audio; React with a build tool adds dependencies and a build step. Native modules keep setup small and behavior inspectable, at the cost of explicit state updates, screen lifecycle and cancellation guards. Frontend controller tests cover those responsibilities.

Implementation: [app](backend/app.py), [routes](backend/api/routes.py), [dependencies](requirements.txt).

## 2. Separate Rooms and Game inside one application

Date: 2026-09-29

Status: Superseded by [6](#6-extract-rooms-and-game-as-separate-services)

Context: Membership and music imports change for different reasons than rounds and scoring. The assignment requires two SQLite-backed domains with a future separation point.

Decision: Rooms owns membership and listening data; Game owns frozen games, attempts, answers and rankings. Application coordination passes `RoomsService.snapshot` values into `GameService.start`.

Alternatives considered: One combined service couples provider changes to gameplay. Separately deployed services add network coordination and conflict with the single-process constraint.

Consequences: Game uses stable snapshots and its own repository. A future service split will need an explicit consistency strategy to replace the shared transaction.

Update (2026-10-04): Spotify and Demo translate input through `MusicSource`, then share `MusicImporter`, `MusicAdmissionHandler` and Rooms persistence. Provider calls happen before the admission transaction. [Architecture](docs/05_ARCHITECTURE.md) explains these boundaries and their course-pattern rationale.

## 3. Room-local songs and frozen game facts in SQLite

Date: 2026-09-30

Status: Decided

Context: Players can share recordings, but each room needs independent expiry. Historical listeners and scores must survive disconnects and later lobby changes.

Decision: Store room-local `songs`/`player_songs`, freeze roster and song-plan facts at Start, and persist attempts, answers and receipts in `DATA_DIR/whos_on_repeat.sqlite3`. Expire rooms 30 days after their last completed game, or creation if none completes.

Alternatives considered: Global listening records complicate room deletion; live membership lookups change old games. Fully relational snapshot tables add joins for facts already transferred as values.

Consequences: Frozen facts stabilize scoring and history, including retained void answers. Services validate JSON references, and transactional cleanup deletes Game data before Rooms data.

Update (2026-10-04): public catalog metadata and verified links share the application database. Preview expiry affects audio availability; recording identity remains stored. See the [schema and diagram](docs/06_DATA_MODEL.md).

## 4. Prioritize core rule tests and verify integration separately

Date: 2026-09-30

Status: Decided

Context: Membership, deadlines, scoring and recovery need reproducible tests. The assignment requires at least 70% unit coverage of both domains' core business logic.

Decision: Use deterministic unit tests and a 90% core coverage gate per domain. Add real SQLite/HTTP tests for persistence and transport, and frontend tests for controller and audio behavior.

Alternatives considered: Broad HTTP tests alone make policy failures harder to isolate. Live-provider tests depend on account access and external availability.

Consequences: Controlled clocks, randomness and repository doubles make policy tests repeatable; integration tests cover transactions and complete matches. Device audio and live accounts require manual checks.

Update (2026-10-04): consolidation gives each behavior a clear test owner. Coverage includes Rooms service and Game service, scoring, selection, preparation and song-title matching. See [test ownership](docs/18_TESTING_STRATEGY.md) and the [verification command](README.md#verify).

## 5. Use host-device audio and keep the first game focused

Date: 2026-10-01

Status: Decided

Context: Shared playback avoids per-device gesture, scheduling and drift problems while the core game is being built. Provider uncertainty also requires a reproducible Demo.

Decision: Play audio on the host device and defer synchronized device audio, mashups, chat and persistent game accounts. Offer explicit Demo and Real modes, with successful personal import required before Real membership.

Alternatives considered: Synchronized playback adds timing and recovery work. Automatically replacing failed personal imports with simulated libraries would change what the game represents.

Consequences: Everyone guesses through their own browser and hears a shared speaker. Demo uses simulated libraries; Real depends on configured provider access.

Evolution: the four-track Oct 1 prototype became a pinned 100-song Demo pack on Oct 4. Spotify personal imports are implemented; Apple provides catalog/previews, with personal listening deferred. The launcher prepares Demo music before starting; bare server readiness works independently of the pack. See [setup](README.md#run) and the [music connection contract](docs/07_API_AND_RUNTIME.md#12-real-music-connection-runtime).

## 6. Extract Rooms and Game as separate services

Date: 2026-11-17

Status: Decided; implementation is incremental

Context: Assignment 2 requires two independently deployed Python services with
separate databases and HTTP communication. ADR-2 correctly separated changing
membership/listening data from frozen gameplay, but its shared coordinator still
uses one transaction for Start, presence and completion.

Decision: Keep the Rooms/Game seam. Move the existing domain implementations into
`services/rooms/domain` and `services/game/domain`, with independent FastAPI
composition roots and pinned manifests. Rooms owns membership, music admission,
public catalog/search and media. Game owns frozen rosters/song facts, plans,
rounds, answers, results and audio leases. Share only pure error/recording-value
helpers and small runtime infrastructure, never another domain's repository.

Rooms will coordinate Start server-side: persist the accepted operation and lock
admission, send its frozen snapshot to Game with a stable request identity, then
reconcile completion. A lost response must leave an operation recoverable by that
identity; it must not reopen admission blindly. Calls have bounded timeouts, and
dependency failure returns a clear retryable response while each service keeps
serving its own domain. Game must not read Rooms tables. The UI will call both
services using configured URLs. Internal snapshot/import endpoints require
service authentication, and snapshot export additionally requires the host's
room credential. Public views never expose private listening evidence.

Alternatives considered: Moving the seam to catalog/game would mix membership
with frozen scoring and abandon ADR-2. A third gateway, broker or distributed
transaction adds infrastructure outside the assignment. A route-filtered copy
of the monolith would still initialize both domains and would not establish
independent ownership.

Consequences: The frozen snapshot contract survives. The shared transaction and
cross-domain foreign keys do not; durable command identities and reconciliation
must replace their guarantees. Day-one applications use separate development
SQLite schemas and trusted internal APIs. The existing monolith remains the
playable compatibility entry point until SQL Server and HTTP lifecycle integration
are complete. These applications are extraction checkpoints, not the finished
Azure deployment. SQL Server transfer/recovery details belong in ADR-7.

Implementation contract: [service split](docs/20_SERVICE_SPLIT.md). Schedule and
acceptance evidence: [Assignment 2 plan](docs/19_ASSIGNMENT_2_PLAN.md).

## 7. Isolate SQL Server storage and transfer legacy data from a backup

- **Date:** 2026-11-18
- **Status:** Accepted for the separate storage layer; hosted cutover is pending.
- **Supersedes:** ADR-3 for the standalone applications. ADR-3 still describes the
  Assignment 1 compatibility runtime.

**Decision.** Rooms and Game each own a SQL Server 2022 database, login and
migration ledger. They share connection and migration code. Each startup checks
applied scripts against their SHA-256 receipts before updating the schema, and
commits each new script with its receipt. Use a new script to correct an applied
version. Each API checks its own tables and a write/read/delete probe. Health
requires neither the peer service nor its database.

**Boundary.** Game stores room and player identities as values from the frozen
snapshot. It keeps foreign keys between its own games, frozen players, rounds,
answers and receipts. The Rooms deletion boundary is a service operation: a
Game transaction deletes dependent answers before deleting the games for a room.
SQL Server disallows the two cascades into answers that SQLite supported. Existing
snapshots and scores never depend on a live nickname, song or membership.
Cross-service Start and lifecycle reconciliation use the
[HTTP service contracts](docs/20_SERVICE_SPLIT.md#http-lifecycle-milestone--2026-11-18).

**Transfer and recovery.** Quiesce the source and both target applications. Use
the SQLite backup API to make a stable Assignment 1 schema v6 copy. Leave the
source intact and start with initialized, empty SQL targets. The transfer checks
both targets before writing, copies only the tables each service owns, verifies
the rows, and commits data and the backup fingerprint in one transaction **per
database**. It preserves room sessions, active and historical games, frozen
facts, round state, scores and command receipts. Repo-managed Demo seed and
derived provider caches are rebuilt separately. No SQL transaction spans both
domains.

If one commit fails or its response is lost, leave both services offline and
retry with the identical backup and the same databases. A matching receipt is
accepted only if the target rows still match. A nonempty target without a
matching receipt is rejected; the tool never overwrites it. Do not use this as
live replication after cutover. Until the full HTTP flow is verified, keep the
old deployment and backup available as the rollback path.

**Alternatives.** A shared schema would let either login access the other
domain's records. Live dual writes would need conflict recovery while games are
changing. A checkpointed, verified copy keeps the original usable and lets an
operator resume a failed transfer without guessing which transaction committed.

See the [transfer runbook](docs/21_ASSIGNMENT_1_TRANSFER.md) for commands,
verification and cutover conditions.

## 8. Run the split services on Azure serverless infrastructure

- **Date:** 2026-11-20
- **Updated:** 2026-11-22 for the revised Azure course guidance.
- **Status:** Accepted for the Azure baseline.

**Context.** The browser calls both services over HTTPS. Each service needs a
durable database. The revised course guide uses the student's own Azure for
Students subscription and a dedicated resource group under the university tenant.

**Decision.** Use one Container Apps Consumption environment with Rooms and
Game at zero to one replica each. Use separate General Purpose serverless
databases on one SQL logical server. Enable the free offer with 0.5–1 vCore,
locally redundant backups and `AutoPause` when the free limit is exhausted.
The offer supplies the 60-minute idle delay and 32 GB size limit. Each API
uses its own contained SQL user over TLS. Host `ui/`
through the Azure Storage static website endpoint, and allow that exact origin
in both APIs. Keep the Log Analytics workspace for container and request logs.
The pipeline publishes public, commit-tagged images to GHCR and deploys that
same SHA. Use GitHub Actions OIDC to authenticate a user-assigned managed
identity with Contributor scoped to the student's resource group. Its two
federated subjects restrict trust to the named and immutable identities for
this repository's `main` branch. SQL passwords and the service token come
from GitHub Actions secrets and Container Apps uses secret references.

**Alternatives.** Always-on compute and provisioned databases would reduce cold
starts by trading idle costs for fixed expenditure. A shared database would
couple deploys and let one service access data owned by the other. An Azure
service-principal client secret would require credential storage and rotation.

**Consequences.** The initial HTTP request can trigger replica startup and
database resume. SQL startup retries transient failures with backoff before
applying migrations. Deployment gives each own-database readiness check a
210-second budget. Each database is unavailable for the remainder of a month
if its free compute limit is exhausted. Other Azure services still use their
own consumption and storage allowances. Newly published GHCR packages need a
one-time visibility change so Azure can pull them anonymously. The application
keeps its three-second peer timeout and can retry an unresolved Start by its
saved identity. The SQL startup logs and
`/health` version identify the deployed revision. At substantially higher
traffic, increase the database capacity based on measured load, take migrations
out of per-replica startup, and give the Rooms reconciliation worker explicit
ownership before raising the replica maximum.

See the [Azure deployment runbook](docs/22_AZURE_DEPLOYMENT.md).

## 9. Gate deployment on service tests, contracts and image builds

Date: 2026-11-20

Status: Decided

Context: The split adds HTTP and persistence boundaries that monolith unit tests
cannot exercise. Each service image and both machine-readable contracts must
also match the source we deploy.

Decision: Run the [CI workflow](.github/workflows/ci.yml) on every push and pull
request. Each service installs its own dependencies, runs lint and tests, and
enforces 90% core coverage. The regression job checks the legacy app, frontend,
both committed OpenAPI documents, and the system manifest against the
professor's schema. The manifest check also verifies that its HTTP calls resolve
to operations in the target OpenAPI. Independent Linux image builds verify both
Dockerfiles. A push to main starts deployment only after all checks pass for
that commit.

The cross-service test starts Rooms and Game as separate processes against
scoped databases in a disposable SQL Server container. Over loopback HTTP it
starts a match through Rooms, checks that Game received the frozen state, and
exercises retries, reconciliation and the remaining API's health when its peer
stops. The fault tests run both real ASGI applications in process and inject
transport failures at precise points, including after Game commits but before
Rooms receives the response. That covers ambiguous outcomes without relying on
a real network interruption occurring at the intended instant.

Alternatives considered: Testing only with stubs would miss real SQL Server and
HTTP behavior. Reproducing every network fault with live processes would add
timing dependence. Running CI against Azure SQL would consume the student's
allowance and expose persistent data to test cleanup.

Consequences: CI runs with disposable SQL data and generated credentials. It
leaves Azure routing, cold starts, live persistence, and physical audio output to
deployment and acceptance checks. The proof of a live data-preserving migration
comes from the deployed system rather than the CI database.
