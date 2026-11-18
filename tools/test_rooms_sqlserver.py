"""Test both services against a disposable SQL Server using isolated logins."""

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import urlopen

import pymssql

from tools.setup_rooms_db import initialize

ROOT = Path(__file__).resolve().parents[1]


def process_smoke(owner, url):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {
        **os.environ,
        "DATABASE_URL": url,
        "PORT": str(port),
        "GIT_SHA": "sql-process-test",
        "SERVICE_TOKEN": secrets.token_urlsafe(30),
    }
    with tempfile.TemporaryFile(mode="w+") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", f"services.{owner}"],
            cwd=ROOT,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 30
            while True:
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"Standalone {owner} process did not become healthy"
                    )
                try:
                    with urlopen(
                        f"http://127.0.0.1:{port}/health", timeout=5
                    ) as response:
                        health = json.load(response)
                    assert health == {
                        "status": "ok",
                        "db": "ok",
                        "version": "sql-process-test",
                    }
                    break
                except (URLError, TimeoutError):
                    time.sleep(0.1)
            output.seek(0)
            assert f"Applied {owner.title()} migration 001_init.sql" in output.read()
            print(
                f"Standalone {owner.title()} SQL Server startup, health and migration log passed"
            )
        except Exception:
            output.seek(0)
            print(output.read(), file=sys.stderr)
            raise
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main():
    name = "repeat-rooms-test-" + secrets.token_hex(4)
    password = "Aa9!" + secrets.token_urlsafe(30)
    passwords = {
        owner: "Aa9!" + secrets.token_urlsafe(30) for owner in ("rooms", "game")
    }
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {**os.environ, "MSSQL_SA_PASSWORD": password}
    subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "--platform",
            "linux/amd64",
            "--publish",
            f"127.0.0.1:{port}:1433",
            "--env",
            "ACCEPT_EULA=Y",
            "--env",
            "MSSQL_PID=Developer",
            "--env",
            "MSSQL_MEMORY_LIMIT_MB=2048",
            "--env",
            "MSSQL_SA_PASSWORD",
            "mcr.microsoft.com/mssql/server:2022-latest",
        ],
        env=env,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 120
        while True:
            try:
                raw = pymssql.connect(
                    server="127.0.0.1",
                    port=str(port),
                    user="sa",
                    password=password,
                    database="master",
                    login_timeout=2,
                    timeout=5,
                    autocommit=True,
                )
                break
            except pymssql.OperationalError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Local SQL Server did not become ready"
                    ) from None
                time.sleep(1)
        raw.close()
        admin_url = f"mssql+pymssql://sa:{quote(password, safe='')}@127.0.0.1:{port}/master?encryption=off"
        initialize(
            admin_url,
            **{f"{owner}_password": value for owner, value in passwords.items()},
        )
        initialize(
            admin_url,
            **{f"{owner}_password": value for owner, value in passwords.items()},
        )
        test_env = dict(os.environ)
        for owner, service_password in passwords.items():
            url = f"mssql+pymssql://repeat_{owner}_user:{quote(service_password, safe='')}@127.0.0.1:{port}/repeat_{owner}?encryption=off"
            guard = secrets.token_hex(32)
            with pymssql.connect(
                server="127.0.0.1",
                port=str(port),
                user="sa",
                password=password,
                database=f"repeat_{owner}",
                autocommit=True,
            ) as admin:
                cursor = admin.cursor()
                cursor.execute(
                    f"CREATE TABLE {owner}_test_guard (marker CHAR(64) NOT NULL)"
                )
                cursor.execute(f"INSERT INTO {owner}_test_guard VALUES (%s)", (guard,))
            process_smoke(owner, url)
            test_env[f"{owner.upper()}_TEST_DATABASE_URL"] = url
            test_env[f"{owner.upper()}_TEST_GUARD"] = guard
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-x",
                "services/rooms/tests/test_sqlserver.py",
                "services/game/tests/test_sqlserver.py",
                "tests/integration/test_service_processes.py::test_two_sql_server_service_processes_reconcile_lifecycle",
            ],
            cwd=ROOT,
            env=test_env,
        )
        return result.returncode
    finally:
        subprocess.run(
            ["docker", "stop", "--time", "5", name],
            check=False,
            stdout=subprocess.DEVNULL,
        )
        print("Stopped disposable SQL Server test container " + name)


if __name__ == "__main__":
    raise SystemExit(main())
