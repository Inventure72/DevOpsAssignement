"""Isolated SQLite runtime for extraction; production SQL Server is a later step."""

import hashlib
import os
import re
import secrets
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse

from shared.contracts import ErrorResponse, Health
from shared.errors import DomainError


@dataclass(frozen=True)
class ServiceConfig:
    database_url: str
    service_token: str = ""
    version: str = "development"

    @classmethod
    def from_env(cls, name):
        return cls(
            os.environ.get("DATABASE_URL", f"sqlite:///data/{name}.sqlite3"),
            os.environ.get("SERVICE_TOKEN", ""),
            os.environ.get("GIT_SHA", "development"),
        )


class DevelopmentDatabase:
    def __init__(self, url, migrations, *, required_tables):
        if not url.startswith("sqlite:///") or not url.removeprefix("sqlite:///"):
            raise ValueError("Extraction runtime requires a sqlite:/// DATABASE_URL")
        self.path = Path(url.removeprefix("sqlite:///"))
        self.migrations = Path(migrations)
        self.required_tables = tuple(required_tables)
        if not self.required_tables or any(
            not re.fullmatch(r"[a-z][a-z0-9_]*", table)
            for table in self.required_tables
        ):
            raise ValueError("Explicit domain table names are required")

    @contextmanager
    def read(self):
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        with self.read() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def initialize(self):
        migrations = sorted(self.migrations.glob("*.sql"))
        if not migrations:
            raise RuntimeError("Development migration files are missing")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.read() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(name TEXT PRIMARY KEY, sha256 TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS health_probe (id TEXT PRIMARY KEY)"
            )
            for migration in migrations:
                sql = migration.read_text()
                digest = hashlib.sha256(sql.encode()).hexdigest()
                prior = conn.execute(
                    "SELECT sha256 FROM schema_migrations WHERE name=?",
                    (migration.name,),
                ).fetchone()
                if prior:
                    if prior["sha256"] != digest:
                        raise RuntimeError("An applied development migration changed")
                    continue
                try:
                    conn.executescript("BEGIN IMMEDIATE;\n" + sql)
                    conn.execute(
                        "INSERT INTO schema_migrations VALUES (?,?)",
                        (migration.name, digest),
                    )
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
        # Migration receipts alone cannot prove that the domain schema is intact.
        self.check()

    def check(self):
        with self.transaction() as conn:
            for table in self.required_tables:
                conn.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone()
            identifier = secrets.token_hex(16)
            conn.execute("INSERT INTO health_probe VALUES (?)", (identifier,))
            conn.execute(
                "SELECT id FROM health_probe WHERE id=?", (identifier,)
            ).fetchone()
            conn.execute("DELETE FROM health_probe WHERE id=?", (identifier,))


def service_app(name, config, migrations, *, required_tables):
    db = DevelopmentDatabase(
        config.database_url, migrations, required_tables=required_tables
    )

    @asynccontextmanager
    async def lifespan(app):
        db.initialize()
        yield

    app = FastAPI(
        title=f"Who's On Repeat: {name}",
        lifespan=lifespan,
        responses={
            code: {"model": ErrorResponse}
            for code in (400, 401, 403, 404, 409, 410, 503)
        },
    )
    app.state.db = db

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse(
            {"error": {"code": exc.code, "message": exc.message}},
            status_code=exc.status,
        )

    @app.get(
        "/health",
        responses={503: {"model": Health, "description": "Own database unavailable"}},
    )
    def health() -> Health:
        try:
            db.check()
        except (sqlite3.Error, OSError):
            return JSONResponse(
                {
                    "status": "unavailable",
                    "db": "unavailable",
                    "version": config.version,
                },
                status_code=503,
            )
        return Health(status="ok", db="ok", version=config.version)

    def internal(x_service_token: str | None = Header(default=None)):
        if not config.service_token:
            raise DomainError(
                "service_unconfigured", "Internal API is not configured", 503
            )
        if x_service_token is None or not secrets.compare_digest(
            x_service_token.encode(), config.service_token.encode()
        ):
            raise DomainError(
                "service_unauthorized", "A service credential is required", 401
            )

    return app, db, internal
