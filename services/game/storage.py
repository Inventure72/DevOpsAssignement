"""Game-owned SQL Server storage."""

from pathlib import Path

from shared.sqlserver import SqlServerDatabase

REQUIRED_TABLES = (
    "games",
    "game_players",
    "rounds",
    "answers",
    "game_commands",
    "game_round_preparations",
)


class GameDatabase(SqlServerDatabase):
    def __init__(self, url, *, migrations=None):
        super().__init__(
            url,
            owner="game",
            migrations=migrations or Path(__file__).parent / "migrations",
            required_tables=REQUIRED_TABLES,
        )
