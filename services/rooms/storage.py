"""Rooms SQL Server schema and Demo catalog startup."""

import logging
from pathlib import Path

from services.rooms.domain.demo import seed_demo
from shared.sqlserver import SqlServerDatabase

logger = logging.getLogger(__name__)
REQUIRED_TABLES = ("rooms", "players", "songs", "player_songs", "demo_catalog")


class RoomsDatabase(SqlServerDatabase):
    def __init__(self, url, *, migrations=None, seed=None):
        super().__init__(
            url,
            owner="rooms",
            migrations=migrations or Path(__file__).parent / "migrations",
            required_tables=REQUIRED_TABLES,
        )
        self.seed = (
            Path(seed)
            if seed
            else Path(__file__).resolve().parents[2] / "catalog/demo_catalog.json"
        )

    def initialize(self):
        super().initialize()
        with self.transaction() as conn:
            seed_demo(conn, self.seed)
        logger.info("Rooms Demo catalog seeded")
