"""Start a disposable local SQL Server and exercise Rooms using a scoped login."""

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


def process_smoke(url):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {
        **os.environ,
        "DATABASE_URL": url,
        "PORT": str(port),
        "GIT_SHA": "sql-process-test",
    }
    with tempfile.TemporaryFile(mode="w+") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "services.rooms"],
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
                        "Standalone Rooms process did not become healthy"
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
            assert "Applied Rooms migration 001_init.sql" in output.read()
            print(
                "Standalone Rooms SQL Server startup, health and migration log passed"
            )
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
    rooms_password = "Aa9!" + secrets.token_urlsafe(30)
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
        try:
            # A peer database provides a real negative authorization check.
            raw.cursor().execute("CREATE DATABASE repeat_other")
        finally:
            raw.close()
        admin_url = f"mssql+pymssql://sa:{quote(password, safe='')}@127.0.0.1:{port}/master?encryption=off"
        initialize(admin_url, rooms_password)
        initialize(admin_url, rooms_password)
        url = f"mssql+pymssql://repeat_rooms_user:{quote(rooms_password, safe='')}@127.0.0.1:{port}/repeat_rooms?encryption=off"
        guard = secrets.token_hex(32)
        with pymssql.connect(
            server="127.0.0.1",
            port=str(port),
            user="sa",
            password=password,
            database="repeat_rooms",
            autocommit=True,
        ) as admin:
            cursor = admin.cursor()
            cursor.execute("CREATE TABLE rooms_test_guard (marker CHAR(64) NOT NULL)")
            cursor.execute("INSERT INTO rooms_test_guard VALUES (%s)", (guard,))
        process_smoke(url)
        test_env = {
            **os.environ,
            "ROOMS_TEST_DATABASE_URL": url,
            "ROOMS_TEST_GUARD": guard,
        }
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-x",
                "services/rooms/tests/test_sqlserver.py",
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
