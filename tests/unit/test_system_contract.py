"""Exercise the CI manifest gate with the official assignment schema."""

import json
from pathlib import Path

import pytest
import yaml

from tools.check_system_contract import SCHEMA_HEADER, check_system_contract


@pytest.fixture
def manifest(tmp_path):
    schema = Path(__file__).resolve().parents[2] / "system.schema.json"
    (tmp_path / schema.name).write_bytes(schema.read_bytes())
    services = {}
    for name, port in (("rooms", 8001), ("game", 8002)):
        path = tmp_path / "services" / name
        path.mkdir(parents=True)
        (path / "openapi.json").write_text(
            json.dumps(
                {"paths": {"/health": {"get": {}}, "/internal/games": {"post": {}}}}
            )
        )
        services[name] = {
            "path": f"services/{name}",
            "openapi": f"services/{name}/openapi.json",
            "port_default": port,
            "migrations": f"services/{name}/migrations",
            "env": {
                "PORT": {"description": "Listen port", "default": port},
                "DATABASE_URL": {"description": "Database connection", "secret": True},
            },
            "azure": {
                "container_app": f"student-{name}",
                "sql_database": f"student-{name}",
            },
        }
    rooms = services["rooms"]
    rooms["env"]["GAME_BASE_URL"] = {"description": "Game origin", "default": ""}
    rooms["calls"] = [
        {
            "service": "game",
            "url_env": "GAME_BASE_URL",
            "endpoints": ["POST /internal/games"],
            "timeout_s": 3,
            "on_failure": "Start remains pending",
        }
    ]
    return {
        "student": "student",
        "prefix": "student",
        "public_url": "https://studentui.example",
        "ui": {
            "path": "ui/",
            "storage_account": "studentui",
            "calls": ["rooms", "game"],
        },
        "azure": {
            "resource_group": "rg-sdd-3a",
            "container_apps_environment": "student-apps",
            "sql_server": "student-sql",
            "registry": "ghcr.io/student",
        },
        "services": services,
    }


def write_manifest(path, value):
    (path / "system.yaml").write_text(SCHEMA_HEADER + "\n" + yaml.safe_dump(value))


def test_valid_manifest_passes_official_schema_and_call_check(tmp_path, manifest):
    write_manifest(tmp_path, manifest)
    check_system_contract(tmp_path)


def test_secret_defaults_fail_without_echoing_the_value(tmp_path, manifest):
    manifest["services"]["rooms"]["env"]["DATABASE_URL"]["default"] = "secret-canary"
    write_manifest(tmp_path, manifest)
    with pytest.raises(
        ValueError, match=r"services\.rooms\.env\.DATABASE_URL"
    ) as error:
        check_system_contract(tmp_path)
    assert "secret-canary" not in str(error.value)


def test_unlisted_openapi_endpoint_fails_the_gate(tmp_path, manifest):
    manifest["services"]["rooms"]["calls"][0]["endpoints"] = ["POST /internal/missing"]
    write_manifest(tmp_path, manifest)
    with pytest.raises(ValueError, match="missing from game OpenAPI"):
        check_system_contract(tmp_path)
