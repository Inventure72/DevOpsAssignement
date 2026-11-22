"""Shared SQL Server connections and transactional migrations."""

import hashlib
import ipaddress
import logging
import os
import re
import secrets
import time
from contextlib import contextmanager, suppress
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from urllib.parse import parse_qs, unquote, urlsplit

import pymssql

logger = logging.getLogger(__name__)
DRIVER_ERRORS = (pymssql.Error, pymssql._mssql.MSSQLException)
CONNECT_LOCK = Lock()


def valid_host(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return re.fullmatch(r"[a-z0-9][a-z0-9.-]*", host) is not None


def connection_options(url):
    try:
        parts = urlsplit(url)
        options = parse_qs(parts.query, strict_parsing=True)
        if (
            parts.scheme != "mssql+pymssql"
            or not parts.hostname
            or not valid_host(parts.hostname)
            or not parts.username
            or not parts.password
            or not parts.path.strip("/")
            or parts.fragment
            or set(options) - {"encryption"}
        ):
            raise ValueError
        encryption = options.get("encryption", ["require"])
        if encryption not in (["require"], ["off"]):
            raise ValueError
        if encryption == ["off"] and parts.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError
        options = dict(
            server=parts.hostname,
            port=str(parts.port or 1433),
            user=unquote(parts.username),
            password=unquote(parts.password),
            database=unquote(parts.path.lstrip("/")),
            charset="UTF-8",
            as_dict=True,
            timeout=5,
            login_timeout=5,
            tds_version="7.4",
            encryption=encryption[0],
            autocommit=False,
        )
        if any("\x00" in options[key] for key in ("user", "password", "database")):
            raise ValueError
        return options
    except (ValueError, TypeError):
        raise ValueError(
            "DATABASE_URL must be a valid mssql+pymssql URL; encryption is required except for local SQL Server"
        ) from None


def connect_sql(options):
    with CONNECT_LOCK:
        if options["encryption"] == "off":
            return pymssql.connect(**options)
        hostname = options["server"]
        alias = "repeat-sql-" + secrets.token_hex(8)
        with NamedTemporaryFile(mode="w", encoding="ascii") as config:
            config.write(
                f"[{alias}:{options['port']}]\nhost = {hostname}\nport = {options['port']}\n"
                f"ca file = system\ncheck certificate hostname = yes\ncertificate hostname = {hostname}\n"
            )
            config.flush()
            previous = os.environ.get("FREETDSCONF")
            os.environ["FREETDSCONF"] = config.name
            try:
                return pymssql.connect(**{**options, "server": alias})
            finally:
                if previous is None:
                    del os.environ["FREETDSCONF"]
                else:
                    os.environ["FREETDSCONF"] = previous


class Connection:
    """Adapt parameter markers and preserve strings containing embedded NULs."""

    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, parameters=()):
        cursor = self.raw.cursor()
        markers = []
        bound = []
        for value in parameters:
            special = isinstance(value, str) and "\x00" in value
            markers.append("CONVERT(nvarchar(max),%s)" if special else "%s")
            bound.append(value.encode("utf-16-le") if special else value)
        replacements = iter(markers)
        statement = re.sub(r"\?", lambda _: next(replacements), sql) if markers else sql
        cursor.execute(statement, tuple(bound) if bound else None)
        return cursor

    def executemany(self, sql, values):
        batch = list(values)
        if any(
            isinstance(value, str) and "\x00" in value for row in batch for value in row
        ):
            for row in batch:
                self.execute(sql, row).close()
        else:
            cursor = self.raw.cursor()
            cursor.executemany(sql.replace("?", "%s"), batch)
            cursor.close()


class SqlServerDatabase:
    errors = (*DRIVER_ERRORS, OSError, ValueError)

    def __init__(self, url, *, owner, migrations, required_tables):
        self._url = url
        self.owner = owner
        self.migrations = Path(migrations)
        self.required_tables = tuple(required_tables)

    def _connect(self, *, retry=False):
        options = connection_options(self._url)
        for attempt in range(8 if retry else 1):
            raw = None
            try:
                raw = connect_sql(options)
                cursor = raw.cursor()
                cursor.execute("""SET NOCOUNT ON; SET XACT_ABORT ON;
                    SET ANSI_NULLS ON; SET QUOTED_IDENTIFIER ON; SET ANSI_PADDING ON;
                    SET ANSI_WARNINGS ON; SET ARITHABORT ON; SET CONCAT_NULL_YIELDS_NULL ON;
                    SET NUMERIC_ROUNDABORT OFF;
                    SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;""")
                raw.commit()
                return raw
            except BaseException as error:
                if raw is not None:
                    with suppress(*DRIVER_ERRORS):
                        raw.close()
                if not isinstance(error, pymssql.OperationalError):
                    raise
                # Only transient SQL/Azure resume or connection failures retry;
                # invalid credentials and permissions fail immediately.
                codes = {
                    int(n)
                    for n in re.findall(rb"\b\d{4,5}\b", str(error.args).encode())
                }
                if (
                    not retry
                    or attempt == 7
                    or not codes
                    & {40613, 40197, 40501, 10928, 10929, 20002, 20009, 10054}
                ):
                    raise
                logger.warning("SQL startup connection retry %d/8", attempt + 1)
                time.sleep(min(2**attempt, 8))

    def read(self):
        return self._transaction("Shared", commit=False)

    def transaction(self):
        return self._transaction("Exclusive")

    @contextmanager
    def _transaction(self, lock_mode=None, *, commit=True):
        raw = self._connect()
        try:
            if lock_mode:
                cursor = raw.cursor()
                cursor.execute(
                    """DECLARE @result INT; EXEC @result=sys.sp_getapplock
                    @Resource=%s, @LockMode=%s, @LockOwner='Transaction',
                    @LockTimeout=5000; SELECT @result AS result;""",
                    (f"{self.owner}-write", lock_mode),
                )
                if cursor.fetchone()["result"] < 0:
                    raise pymssql.OperationalError(
                        f"{self.owner.title()} command lock unavailable"
                    )
            yield Connection(raw)
            if commit:
                raw.commit()
            else:
                raw.rollback()
        except BaseException:
            with suppress(*DRIVER_ERRORS):
                raw.rollback()
            raise
        finally:
            with suppress(*DRIVER_ERRORS):
                raw.close()

    def initialize(self):
        label = self.owner.title()
        files = sorted(self.migrations.glob("*.sql"))
        if (
            not files
            or any(not re.fullmatch(r"\d{3}_[a-z0-9_]+\.sql", p.name) for p in files)
            or len({p.name[:3] for p in files}) != len(files)
        ):
            raise RuntimeError(f"{label} migration files are missing or invalid")
        scripts = []
        for path in files:
            sql = path.read_text(encoding="utf-8")
            scripts.append((path.name, sql, hashlib.sha256(sql.encode()).hexdigest()))
        raw = self._connect(retry=True)
        try:
            cursor = raw.cursor()
            cursor.execute(
                """DECLARE @result INT; EXEC @result=sys.sp_getapplock
                @Resource=%s, @LockMode='Exclusive', @LockOwner='Session',
                @LockTimeout=10000; SELECT @result AS result;""",
                (f"{self.owner}-migrations",),
            )
            if cursor.fetchone()["result"] < 0:
                raise RuntimeError(f"{label} migration lock unavailable")
            cursor.execute("""IF OBJECT_ID('dbo.schema_migrations','U') IS NULL
                CREATE TABLE schema_migrations (
                    name VARCHAR(128) PRIMARY KEY, sha256 CHAR(64) NOT NULL,
                    applied_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME());""")
            raw.commit()
            cursor.execute("SELECT name,sha256 FROM schema_migrations")
            applied = {r["name"]: r["sha256"] for r in cursor.fetchall()}
            prefix = scripts[: len(applied)]
            if set(applied) != {name for name, _, _ in prefix}:
                raise RuntimeError(
                    f"Applied {label} migrations must match the ordered file prefix"
                )
            if any(applied[name] != digest for name, _, digest in prefix):
                raise RuntimeError(f"An applied {label} migration changed")
            for name, sql, digest in scripts[len(applied) :]:
                cursor.execute(sql)
                while cursor.nextset():
                    pass
                cursor.execute(
                    "INSERT INTO schema_migrations(name,sha256) VALUES (%s,%s)",
                    (name, digest),
                )
                raw.commit()
                logger.info("Applied %s migration %s", label, name)
        except BaseException:
            with suppress(*DRIVER_ERRORS):
                raw.rollback()
            raise
        finally:
            with suppress(*DRIVER_ERRORS):
                raw.close()
        self.check()

    def check(self):
        with self._transaction() as conn:
            for table in self.required_tables:
                conn.execute(f"SELECT TOP (1) 1 AS present FROM {table}").fetchone()
            identifier = secrets.token_hex(16)
            conn.execute("INSERT INTO health_probe(id) VALUES (?)", (identifier,))
            if (
                conn.execute(
                    "SELECT id FROM health_probe WHERE id=?", (identifier,)
                ).fetchone()
                is None
            ):
                raise pymssql.OperationalError("Database health read failed")
            conn.execute("DELETE FROM health_probe WHERE id=?", (identifier,))
