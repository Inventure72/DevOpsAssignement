# Transfer Assignment 1 data

Use the transfer once during a maintenance window, before allowing either SQL
Server service to accept traffic. The tool expects a **stable SQLite schema v6
backup** and two initialized SQL Server databases with empty domain tables.
Existing SQL rows are never overwritten. Keep the old application and database
available until the new workflow passes acceptance.

1. Stop writes from the old app. Make a separate, access-restricted SQLite
   backup using SQLite's backup facility. For example, run
   `sqlite3 /path/to/old.sqlite3 '.backup /safe/path/assignment1-backup.sqlite3'`.
   Keep the backup independent of the original directory. Check that the backup
   has `PRAGMA user_version = 6` and that `PRAGMA foreign_key_check` returns no
   rows. Do not pass a live file with a pending WAL or rollback journal.
2. Provision two databases and distinct logins. For the local SQL Server, use
   `python -m tools.setup_rooms_db` with `SQL_ADMIN_URL`, `ROOMS_DB_PASSWORD` and
   `GAME_DB_PASSWORD` supplied securely through the environment. Use a scoped
   login for each service. This helper is not the hosted Azure bootstrap.
3. Supply `ROOMS_DATABASE_URL` and `GAME_DATABASE_URL` with the respective scoped
   credentials and initialize both migration ledgers while the apps are offline:

   ```sh
   python -c 'import os; from services.rooms.storage import RoomsDatabase; RoomsDatabase(os.environ["ROOMS_DATABASE_URL"]).initialize()'
   python -c 'import os; from services.game.storage import GameDatabase; GameDatabase(os.environ["GAME_DATABASE_URL"]).initialize()'
   ```

4. Copy the backup into the initialized targets:

   ```sh
   python -m tools.transfer_assignment1 --source /safe/path/assignment1-backup.sqlite3 --apply
   ```

   The result shows one fingerprint, row counts, and `copied` or
   `already_copied` for both databases. The copy validates exact stored rows,
   including frozen facts and game/roster ordering, before recording a receipt.
   The report contains no stored room or player values.
5. If the copy fails, keep both targets offline. Resolve the reported issue and
   rerun with the same backup. The retry verifies any already committed target
   and copies the missing one. A mismatched receipt or nonempty target is a stop
   condition. Retain those databases for diagnosis and provision fresh targets
   if a new copy is needed. Do not delete or edit the original SQLite database.

The copy includes Rooms membership and stored session hashes, songs and listener
links, plus Game history, active state, round preparation and command receipts.
Rooms startup seeds the current Demo catalog from the committed pack. The copy
does not transfer runtime probes, database migration ledgers, provider caches,
or external media files. Move any separately hosted media before redirecting
traffic, and verify the clock and media paths before resuming an active game.
Do not enable the split browser workflow until server-side lifecycle coordination
is verified.

For a rehearsal that never uses existing data, run
`python -m tools.test_rooms_sqlserver`. It creates a disposable SQL Server,
starts both services with scoped logins, then copies a populated SQLite fixture.
The acceptance test checks a rolled-back second target, a retry, historical scores
and active game state, and confirms the backup bytes remain unchanged.
