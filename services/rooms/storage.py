"""Rooms SQL Server schema and Demo catalog startup."""

import logging
from pathlib import Path

from services.rooms import bundled_demo
from services.rooms.domain.demo import seed_demo
from shared.sqlserver import SqlServerDatabase

logger = logging.getLogger(__name__)
REQUIRED_TABLES = (
    "rooms",
    "players",
    "songs",
    "player_songs",
    "demo_catalog",
    "room_start_operations",
)


class RoomsDatabase(SqlServerDatabase):
    def __init__(self, url, *, migrations=None, seed=None):
        super().__init__(
            url,
            owner="rooms",
            migrations=migrations or Path(__file__).parent / "migrations",
            required_tables=REQUIRED_TABLES,
        )
        self.seed = Path(seed) if seed else bundled_demo.CATALOG

    def initialize(self):
        if self.seed == bundled_demo.CATALOG:
            bundled_demo.validate_media()
        super().initialize()
        with self.transaction() as conn:
            seed_demo(conn, self.seed)
        logger.info("Rooms Demo catalog seeded")
