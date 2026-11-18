# Assignment 2 delivery and commit plan

Planned on 2026-11-17. Deadline: **2026-11-22 at 23:59**.

This is a prospective plan, not evidence that implementation, tests, commits,
pushes or deployments have happened. Assignment 1 history remains unchanged.

## Outcome

Keep Who's On Repeat and its Python/FastAPI stack. Split Rooms and Game into
exactly two domain services, with separate containers, dependency manifests and
SQL Server databases. The static UI calls both services. At least one visible
workflow crosses the boundary through server-to-server HTTP.

The accepted system runs from a clean clone with `docker compose up --build` and
deploys through GitHub Actions to two Azure Container Apps, two serverless Azure
SQL databases and one Azure Static Web App. Preserve gameplay, private listening
data, host audio, existing records and the frozen game snapshot contract.

## Commit budget and safety margin

The brief requires at least 12 new meaningful commits after Assignment 1, across
at least six calendar days, with no day containing more than 40% of them. Push
timestamps are checked. Work on branches and bring changes to main through PRs.

**Target 18 substantive commits: 4 + 4 + 3 + 3 + 2 + 2.**

| Date (2026) | Planned commits | Cumulative | Main goal |
|---|---:|---:|---|
| Tue 17 November | 4 | 4 | Contracts, service extraction and initial CI |
| Wed 18 November | 4 | 8 | Separate SQL storage and HTTP coordination |
| Thu 19 November | 3 | 11 | Complete local compose system |
| Fri 20 November | 3 | 14 | Automated Azure baseline deployment |
| Sat 21 November | 2 | 16 | Migration evidence and system acceptance |
| Sun 22 November | 2 | 18 | Documentation, final evidence and submission |

With 16 commits already completed and pushed before Sunday, Sunday can contain
up to **10 substantive commits total**: the two planned plus **eight extra**.
That produces 26 commits overall, with 10/26 = **38.46%** on Sunday. Eleven on
Sunday against the same 16 earlier commits would be 11/27 = 40.74%, over the limit.

This allowance depends on the actual history. Recalculate before the final day
and before submission: for every date, `commits_on_date / total_commits <= 0.40`.
Count substantive work conservatively; do not rely on merge commits to reach the
minimum or dilute a busy date. Keep pushes on the actual working dates and avoid
holding completed branches locally until the deadline. Do not fabricate dates,
pad history with empty changes or split inseparable changes merely for the count.

All six dates matter: starting on November 17 leaves exactly six calendar days.
There is no spare day for a missed date. Use a consistent timezone and push well
before midnight to avoid ambiguity between local commit dates and push records.

## Daily deliverables

Each numbered item is an independently reviewable commit target. Adjust boundaries
to preserve coherent, working checkpoints. Run relevant checks before committing,
and update AI_USAGE.md locally alongside the work it describes. Keep that log
untracked and ignored until the author reviews, cleans and commits the final
version, as required by the root AGENTS.md instructions.

### Tuesday 17 November — four commits

1. Record this plan, table ownership and service contracts; add **ADR-6: the split**.
   Assign catalog/media responsibilities within the two services; do not introduce
   a third domain service. Specify how shared transactions become recoverable HTTP
   operations and which internal endpoints may access private snapshots.
2. Extract the Rooms application into `services/rooms/`, with its own dependency
   manifest, composition root and focused rule tests.
3. Extract the Game application into `services/game/`, with its own manifest,
   frozen-snapshot contract and focused rule tests. Establish both OpenAPI files.
4. Add initial push/PR CI for the extracted applications and frontend tests, then
   expand its gates as SQL Server, containers and specifications land.

**Checkpoint:** clear ownership, separately startable application boundaries and
initial CI. Resolve Azure access prerequisites today, independently of coding.

### Wednesday 18 November — four commits

**Health-check follow-up requested for tomorrow:** the development SQLite fix is
already committed as `9a09a66` (November 17). During the SQL Server port below,
carry over and verify its guarantees: missing migrations or an incomplete domain
schema must fail startup, and missing required tables or failed own-database
read/write checks must produce `/health` 503. Keep peer availability independent
of health. Rooms carries these guarantees into SQL Server in today's first
milestone; Game's SQL Server adaptation and Azure verification remain pending.

5. Port Rooms storage to SQL Server: initial versioned migration, migration ledger,
   idempotent seed data and repository integration tests.
6. Port Game storage and define/test the non-destructive transfer of Assignment 1
   data into the two databases. Add **ADR-7: data, migrations and recovery**.
   Remove cross-database foreign keys and document their replacement contracts.
7. Implement the server-side Start flow over HTTP, including authenticated internal
   calls, bounded timeouts, request identity, frozen facts and recovery after an
   ambiguous response. Neither service connects to the other's database.
8. Complete lifecycle coordination and cross-seam tests: completion, presence,
   leave/restart behavior, retries and unavailable dependencies. Replace the old
   coordinator's shared-transaction assumptions explicitly.

**Checkpoint:** separate databases, isolated logins and a tested HTTP workflow.
Document the use of a stub in any cross-seam test; retain real SQL integration.

### Thursday 19 November — three commits

9. Add explicit-copy, non-root Dockerfiles with pinned dependencies; root compose,
   one local SQL Server, idempotent `db/init.sql` and a one-off initialization
   service. Make Demo media setup reproducible without provider credentials.
10. Move static files into `ui/`; configure both API base URLs, cross-origin
    authentication, origin-restricted CORS, invitation routes and host audio/media
    paths. Serve these same files locally with a stock web-server image.
11. Add each service's `GET /health` with its own database read/write check and Git
    version, stdout request logs, and bounded startup connection retries for SQL
    resume. Keep health independent of the other domain service.

**Checkpoint:** complete playable Demo from a clean clone using compose, including
private/incognito player sessions. Stop either domain service and verify the other
still serves its own domain; the UI reports cross-seam failure clearly.

### Friday 20 November — three commits

12. Complete CI: install/lint/test both services, >=70% core coverage per service,
    SQL Server integration, cross-seam tests, frontend checks, system-schema and
    OpenAPI validation, and both Docker builds. Add **ADR-9: pipeline and testing**.
13. Add Azure infrastructure and CD: GHCR images tagged by SHA, two Container Apps
    on Consumption with 0–1 replicas, two prescribed serverless databases on one
    logical server, separate database credentials, and pipeline-managed secrets.
    Add **ADR-8: hosting, cost, cold starts and scaling**. Do not create additional
    environments or resources outside the student's prefix and class group.
14. Complete Static Web App deployment and generated UI configuration; gate main
    deployment on CI for the same SHA and run bounded post-deploy health checks.
    Publish the initial working baseline through the pipeline and record its run,
    image SHA and nonsecret data that will be checked after the later migration.

**Checkpoint:** live baseline by Friday evening, with persistent Azure data.
Do not include Saturday's additive migration in this baseline deployment.

### Saturday 21 November — two commits

15. Add and deploy a meaningful additive/backfill migration against an already
    populated table. Preserve rows, verify a second startup reapplies nothing, and
    capture before/after Azure data, the deployment run and startup migration log.
    Never edit an applied migration or recreate storage to make deployment pass.
16. Finish system acceptance and necessary fixes: full gameplay, lost responses,
    partial failure, restart persistence, idle/cold-start recovery, authentication,
    host audio and clean-clone compose. Record observations and limitations.

**Checkpoint:** implementation and deployment accepted by Saturday evening.
Capture required failure evidence and a real failed-and-fixed pipeline run where
available; document its actual cause rather than manufacturing a failure.

### Sunday 22 November — two commits, with room for fixes

17. Finalize README, both OpenAPI documents, `system.yaml`, matching architecture,
    deployment and database diagrams, AI disclosures and the 4–5-page report.
    Add **ADR-10: a deliberately deferred capability and its adoption conditions**.
    Have the author review and clean the local AI_USAGE.md and commit their
    approved version; do not include the draft automatically.
18. Complete submission evidence and any final acceptance corrections. Revalidate
    the combined candidate, confirm the live deployment matches its SHA, and record
    successful CD and migration evidence. A final evidence/documentation commit is
    valid if no source fixes are necessary.

**Target:** final PR merged and accepted deployment by **18:00**, leaving almost
six hours before the deadline for genuine failures. Tag the accepted main commit
`submission`, push the tag and supply that exact full SHA and repository URL.
The tag operation is not itself an extra implementation commit. Keep the live URL
reachable until grades are published.

## Prerequisites and contingency

- Obtain the professor's `system.schema.json`; it was not found in the inspected
  repository. Use it for validation rather than inventing a substitute schema.
- Confirm class resource group (`rg-sdd-3a` or `rg-sdd-3b`), IE username/resource
  prefix and authorized Azure/GitHub access. Transfer credentials through a secret
  channel and store them only in the intended secret stores.
- Check local Docker/SQL Server support early, including the brief's emulation
  requirement on Apple Silicon. An environment issue must not consume Friday's
  deployment window.
- If work slips, prioritize the required Demo end-to-end flow, SQL isolation,
  HTTP failure handling and Azure migration proof. Defer optional improvements;
  preserve existing behavior and report any proposed scope reduction explicitly.
- If Azure access is delayed, continue local storage, compose, tests, specifications
  and pipeline preparation. Live deployment and migration evidence remain blocked
  until access exists; local tests cannot substitute for them.
- Add exactly ADRs 6–10. Preserve ADRs 1–5 except for the brief's permitted
  supersession status. Planned new ADR dates are November 17, 18, 20 and 22,
  exceeding the three-date minimum.
- Recheck actual commit counts, dates and daily pushes each evening. Extra last-day
  commits are allowed only within the actual 40% calculation; the planned budget
  does not guarantee compliance if earlier milestones were missed.

## Completion evidence

- Clean-clone compose startup and a complete game across both service APIs.
- Own-database-only access per service, persistent migrations and idempotent seeds.
- CI passing on the final SHA, with required lint, coverage, contract and build gates.
- Successful main CD using SHA-tagged images, UI configuration and deployed health
  checks; exact deployed/submitted SHA recorded.
- Azure migration proof: existing rows before, startup application log, rows after.
- Designed partial-failure response, cold-start behavior and request logs observed.
- Matching committed system/OpenAPI specifications, diagrams and report.
- At least 12 meaningful new commits over six dates, no date above 40%, daily push
  evidence, exactly five new ADRs across at least three commit dates, updated AI log.
- Pushed `submission` tag on main, matching full SHA, repository URL and live UI URL.
- Review each design and infrastructure file with the student: the written
  comprehension check multiplies the project grade.
