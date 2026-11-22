"""Provision durable credentials and scoped databases for the local stack."""

import json
import os
import secrets
import subprocess
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote

import pymssql

from shared.sqlserver import connect_sql, connection_options
from tools.setup_rooms_db import initialize

ROOT = Path("/run/repeat")
ADMIN = ROOT / "admin"


def write_file(path, data):
    with NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary_path = Path(output.name)
        try:
            output.write(data)
            output.close()
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)


def bootstrap_credentials():
    path = ADMIN / "credentials.json"
    if not path.exists():
        values = {
            key: "Aa9!" + secrets.token_urlsafe(32) for key in ("sa", "rooms", "game")
        }
        values["service_token"] = secrets.token_urlsafe(32)
        write_file(path, json.dumps(values).encode())
    values = json.loads(path.read_text())
    if not (ADMIN / "ready").exists():
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "365",
                "-subj",
                "/CN=sqlserver",
                "-addext",
                "subjectAltName=DNS:sqlserver",
                "-keyout",
                str(ADMIN / "server.key"),
                "-out",
                str(ADMIN / "server.pem"),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        write_file(ADMIN / "sa-password", (values["sa"] + "\n").encode())
        write_file(ADMIN / "ready", b"ready\n")
    return values


def database_url(user, password, database):
    return (
        f"mssql+pymssql://{user}:{quote(password, safe='')}@sqlserver:1433/{database}"
    )


def wait_for_sql(url):
    options = connection_options(url)
    options.update(autocommit=True, login_timeout=3)
    deadline = time.monotonic() + 120
    while True:
        try:
            with connect_sql(options) as connection:
                connection.cursor().execute("SELECT 1 AS ready")
            return
        except pymssql.OperationalError:
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Local SQL Server did not accept a verified TLS connection"
                ) from None
            time.sleep(1)


def main():
    os.umask(0o077)
    values = bootstrap_credentials()
    os.environ["SSL_CERT_FILE"] = str(ADMIN / "server.pem")
    admin_url = database_url("sa", values["sa"], "master")
    wait_for_sql(admin_url)
    initialize(admin_url, values["rooms"], values["game"])
    for owner in ("rooms", "game"):
        url = database_url(f"repeat_{owner}_user", values[owner], f"repeat_{owner}")
        write_file(ROOT / owner / "server.pem", (ADMIN / "server.pem").read_bytes())
        write_file(
            ROOT / owner / "connection.json",
            json.dumps(
                {"DATABASE_URL": url, "SERVICE_TOKEN": values["service_token"]}
            ).encode(),
        )
    print("Local Rooms and Game databases and scoped logins initialized", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (
        OSError,
        ValueError,
        RuntimeError,
        subprocess.SubprocessError,
        pymssql.Error,
    ) as error:
        if isinstance(error, RuntimeError):
            print(str(error), flush=True)
        else:
            print(
                "Local SQL bootstrap failed; check Docker volume access and SQL Server logs.",
                flush=True,
            )
        raise SystemExit(1) from None
