# Who's On Repeat

A music party game: listen to a song, guess its title and identify which players listen to it. Each player answers on their own screen; the **host plays the audio**.

## Assignment 2 local Demo

With Docker and Compose installed, run this from the repository root:

```sh
docker compose up --build
```

Open **http://127.0.0.1:8080** in a modern browser. Create a room and share the
invite link with two other browser sessions on this computer. Each player can
join from a separate tab. The host clicks **Start game** to enable audio, and
everyone submits answers on their own screen.

On Apple Silicon, enable Rosetta emulation in Docker Desktop for the
SQL Server 2022 container. The Rooms and Game APIs listen on
`http://127.0.0.1:8001` and `http://127.0.0.1:8002`; each has a
`/health` endpoint that checks its own database. Set `UI_HOST_PORT`,
`ROOMS_HOST_PORT` or `GAME_HOST_PORT` to change a local port. Compose binds
all three to loopback and configures the matching UI origin automatically.

Use `docker compose logs rooms game` to inspect API requests. Each line
includes the service, method, route template, HTTP status and elapsed time in
milliseconds. Parameters appear as placeholders such as `{room_id}`. An
unrecognized path appears as `<unmatched>`. Request values and credentials
are omitted.

The one-off `db-init` service generates local credentials and a SQL TLS
certificate in Docker volumes. It creates both databases and their scoped
logins before either API starts. Each API mounts only its own connection
configuration. SQL Server has no published port. No `.env` file, host Python
environment or database setup command is needed. `docker compose down` stops
the stack and keeps its volumes; keep the database and configuration volumes
together so the existing logins still match on the next start.

The Rooms API includes a [bundled Demo catalog](catalog/bundled_demo/README.md)
and assigns listening libraries on the server. Demo admission and media work
without an external pack or a provider account.

## Assignment 1 legacy launcher

The legacy Demo includes 100 song previews and simulated listening libraries.
No Spotify account or API keys are needed, and you can test it by yourself in two
browser sessions.

You need **Python 3.12+**, a modern browser and internet access for the first setup. From the project folder, run:

**macOS / Linux**

```bash
bash tools/run_demo.sh --playtest
```

**Windows**

```powershell
py -3 tools/launch_game.py demo --playtest
```

The launcher creates the Python environment, installs dependencies and downloads the music pack (about 98 MiB). Later Demo games work offline. `--playtest` allows two players; regular games require at least three.

### Try a game on one computer

1. Open the **Open on this computer** URL printed by the launcher, normally **http://127.0.0.1:8000/**. Keep this tab as the host.
2. Enter a nickname such as **Host**, select **Demo** and click **Create demo room**.
3. In the lobby, click **Invite a friend** and copy the link.
4. Open the invite in a **private/incognito window or another browser**, enter another nickname such as **Guest**, and click **Join room**. The room code is filled in by the link.
5. Return to the host tab and click **Start game**. This click enables audio; the song plays from the host tab only. Keep both sessions open.
6. In each tab, search for a song, select a result, choose the listeners (or **Nobody**) and submit the answer. Rounds advance to reveal and rankings.

You can also join from a phone or another computer on the same Wi-Fi.

Stop the server with **Ctrl-C** in the terminal. If port 8000 is already in use, add `--port 8001` to your launch command and use the newly printed URL.

Opening the main address shows Create/Join. Refreshing a room's URL reconnects to that room; invitation links open Join.

### Real music and Demo

**Real** uses Spotify listening history and asks new players to connect their music before joining. **Demo** uses simulated libraries and the same gameplay without an external account. An invitation determines the room's mode. Personal Apple Music import is marked **Coming later**; Apple catalog search and previews are used in configured Real games.

Real requires a configured Spotify developer app, approved participants, Apple catalog signing credentials and a shared HTTPS address with a registered OAuth callback. Credentials are excluded from this repository, so Real is disabled during Demo launch. See [Spotify setup](docs/13_SPOTIFY_IMPLEMENTATION.md) and [real-game launch instructions](docs/16_DEVICE_PLAYTEST.md).

**To try Spotify:** email me your name and Spotify account email so I can authorize you and arrange access to the configured game. This does not configure Real mode in your own checkout.

### Music setup troubleshooting

If the automatic download fails, install the music pack manually:

1. Open [Download the 100-song Demo pack](https://drive.google.com/file/d/1nmv-F4OettnXqMj5JZNp_WzO1XZSCpOp/view) and click **Download** in Google Drive (about 98 MiB).
2. Create `catalog/local/downloads/` inside the project folder and save the file there as **`whos-on-repeat-demo-100.zip`**. Keep it zipped.
3. From the project folder, run the installation command for your platform:

**macOS / Linux**

```bash
python3 tools/setup_demo_pack.py --archive catalog/local/downloads/whos-on-repeat-demo-100.zip
```

**Windows**

```powershell
py -3 tools/setup_demo_pack.py --archive catalog/local/downloads/whos-on-repeat-demo-100.zip
```

When the installer reports **Demo music verified**, rerun the Demo launch command. If you saved the ZIP elsewhere, use its actual path in the command.

See [music pack setup](catalog/README.md) for more help. Add `--check` to a launch command to check setup without starting the server.

## Direct server startup

After installing `requirements.txt`, start the server directly:

```bash
python -m backend
```

The server listens on port `8000` and stores SQLite data under `data/`. Set `PORT` and `DATA_DIR` to change these defaults. It can start without music or provider credentials; available modes depend on that setup. For a playable Demo, use the launcher above. Restart the server if you install music while it is running.

## Verify

For the Assignment 2 extraction checks, install the pinned verification tools in
the project environment as well as the application dependencies:

```sh
python -m pip install -r requirements.txt -r requirements-test.txt \
  -r services/rooms/requirements.txt -r services/game/requirements.txt
bash tools/verify_service.sh rooms
bash tools/verify_service.sh game
python -m tools.check_service_contracts
python -m tools.check_system_contract
```

Each independent service enforces 90% core coverage and tests its own database/API.
Regenerate committed OpenAPI after changing a route or DTO with
`python -m tools.check_service_contracts --write`. The checker validates the
OpenAPI specification and fails when the committed files differ from the apps.
The scripts default to `.venv/bin/python`; set `PYTHON=python` to use another
activated environment. Service-specific coverage files avoid overwriting each
other or the full-suite coverage results.

GitHub Actions runs these checks on pushes and PRs, plus the monolith compatibility
suite and frontend tests. A dedicated SQL Server job starts a local container
with generated test credentials and runs real database acceptance checks. Run it
locally with `python -m tools.test_rooms_sqlserver`. The real-process test
exercises the Rooms and Game HTTP seam against this container. Transport-fault
tests separately inject timeouts and lost replies. CI also builds both Docker
images and validates `system.yaml` against the supplied schema and the declared
HTTP calls against OpenAPI. These verification jobs use disposable local data
and require no Azure credentials. The separate deployment job runs only on
pushes to `main` after every verification job passes for the same commit.
See the [Azure deployment runbook](docs/22_AZURE_DEPLOYMENT.md) for
initial setup, credentials and baseline evidence.

With the project Python environment installed and **Node 24+**, run all tests:

```bash
bash tools/verify.sh
```

With the project Python environment activated, measure core unit coverage:

```bash
python -m pytest -q tests/unit \
  --cov=services.rooms.domain.service --cov=services.game.domain.service \
  --cov=services.game.domain.scoring --cov=services.game.domain.selection \
  --cov=services.game.domain.preparation --cov=services.game.domain.song_titles \
  --cov-report=term-missing --cov-fail-under=90
```

Core unit line coverage is **94.79%**: Rooms **94.38%**, Game **94.94%**. The verification script enforces **90% overall and in each domain**, above the assignment's 70% minimum.

The Oct 4 public-source run passed **323 unit + 245 integration = 568 Python tests**, plus **206 frontend tests**. Private acquisition-tool tests are excluded.

Tests use temporary data and offline provider substitutes; no keys or music download are needed. On Windows, run the Python and Node commands from [the verification script](tools/verify.sh). See [testing strategy](docs/18_TESTING_STRATEGY.md) for test scope and further results.

## Architecture

Assignment 2 extraction is in progress on the development branch. Rooms and
Game now live in `services/rooms/domain` and `services/game/domain`. Independent
[Rooms](services/rooms/README.md) and [Game](services/game/README.md) APIs own
separate SQL Server databases with automatic migrations. Rooms seeds the Demo
catalog at startup; Game stores frozen match facts. The existing browser game
still runs through the SQLite monolith using the same domain implementations.
The [Assignment 1 transfer runbook](docs/21_ASSIGNMENT_1_TRANSFER.md) covers
offline copying and recovery. [Rooms coordinates Start, presence and completion
with Game over HTTP](docs/20_SERVICE_SPLIT.md#http-lifecycle-milestone--2026-11-18).

The app runs in one process with two core domains: **Rooms** handles players and membership; **Game** handles rounds and scoring. Both use SQLite. Spotify and Demo share an import and admission path. The browser separates presentation, transport and host audio.

- [Architecture and boundaries](docs/05_ARCHITECTURE.md)
- [Data model](docs/06_DATA_MODEL.md)
- [API and runtime](docs/07_API_AND_RUNTIME.md)
- [Code map](docs/09_CODEBASE_MAP.md)

## Assignment evidence

[ADR.md](ADR.md) records architecture decisions, and the
[Assignment 1 checklist](docs/17_ASSIGNMENT_REVIEW.md) tracks its deliverables.
The root AI_USAGE.md is maintained locally and intentionally ignored during
Assignment 2; the author will review, clean and commit it before final submission.
The [Assignment 2 plan](docs/19_ASSIGNMENT_2_PLAN.md) and
[service boundary](docs/20_SERVICE_SPLIT.md) track the current work.
