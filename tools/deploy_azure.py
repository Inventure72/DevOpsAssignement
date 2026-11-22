"""Deploy the tested main commit using the GitHub Actions OIDC login."""

import json
import mimetypes
import os
import re
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import urlopen

import yaml

from shared.sqlserver import DRIVER_ERRORS, connect_sql, connection_options

ROOT = Path(__file__).resolve().parents[1]
OWNERS = ("rooms", "game")
SQL_ADMIN = "repeat_sql_admin"


def az(*arguments, stdin=None, output=False):
    command = [
        "az",
        *arguments,
        "--only-show-errors",
        "--output",
        "json" if output else "none",
    ]
    result = subprocess.run(
        command, text=True, input=stdin, capture_output=True, check=False
    )
    if result.returncode:
        # Azure diagnostics can contain interpolated parameters. Keep only the error code.
        code = re.search(r"ERROR: \(([\w.-]+)\)", result.stderr)
        detail = f" ({code[1]})" if code else ""
        raise RuntimeError(f"Azure CLI failed: az {' '.join(arguments[:3])}{detail}")
    return json.loads(result.stdout) if output else None


def provision(group, template, parameters, sha):
    payload = {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {name: {"value": value} for name, value in parameters.items()},
    }
    result = az(
        "deployment",
        "group",
        "create",
        "--name",
        f"{parameters['prefix']}-{template}-{sha[:12]}",
        "--resource-group",
        group,
        "--template-file",
        str(ROOT / f"deploy/{template}.bicep"),
        "--parameters",
        "@/dev/stdin",
        "--mode",
        "Incremental",
        "--query",
        "properties.outputs",
        stdin=json.dumps(payload),
        output=True,
    )
    return {name: entry["value"] for name, entry in result.items()}


def sql_url(server, database, user, password):
    return (
        f"mssql+pymssql://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{server}:1433/{quote(database, safe='')}"
    )


def bootstrap_sql(server, database, owner, admin_password, password):
    user = f"repeat_{owner}_user"
    administrator = sql_url(server, database, SQL_ADMIN, admin_password)
    deadline = time.monotonic() + 150
    while True:
        try:
            with connect_sql(connection_options(administrator)) as connection:
                cursor = connection.cursor()
                cursor.execute(
                    f"IF USER_ID('{user}') IS NULL "
                    f"CREATE USER [{user}] WITH PASSWORD = %s, DEFAULT_SCHEMA = dbo",
                    (password,),
                )
                for role in ("db_datareader", "db_datawriter", "db_ddladmin"):
                    cursor.execute(
                        f"IF IS_ROLEMEMBER('{role}', '{user}') <> 1 "
                        f"ALTER ROLE {role} ADD MEMBER [{user}]"
                    )
                connection.commit()
            return sql_url(server, database, user, password)
        except DRIVER_ERRORS as error:
            if time.monotonic() >= deadline:
                code = (
                    error.args[0]
                    if error.args and isinstance(error.args[0], int)
                    else "unknown"
                )
                raise RuntimeError(
                    f"Could not initialize the {owner} SQL user (SQL {code})"
                ) from None
            time.sleep(5)


def publish_ui(storage, ui, origins):
    with TemporaryDirectory() as temporary:
        config = Path(temporary) / "config.js"
        config.write_text(
            "window.REPEAT_CONFIG = " + json.dumps(origins) + ";\n",
            encoding="utf-8",
        )
        for source in sorted(ui.rglob("*")) + [config]:
            if not source.is_file():
                continue
            target = (
                "config.js" if source == config else source.relative_to(ui).as_posix()
            )
            mime = (
                "application/javascript"
                if source.suffix in (".js", ".mjs")
                else mimetypes.guess_type(source)[0] or "application/octet-stream"
            )
            az(
                "storage",
                "blob",
                "upload",
                "--account-name",
                storage,
                "--auth-mode",
                "key",
                "--container-name",
                "$web",
                "--name",
                target,
                "--file",
                str(source),
                "--content-type",
                mime,
                "--content-cache-control",
                "no-cache",
                "--overwrite",
                "true",
            )


def wait_health(owner, origin, sha):
    deadline = time.monotonic() + 210
    while time.monotonic() < deadline:
        try:
            with urlopen(origin + "/health", timeout=8) as response:
                body = json.load(response)
                if body == {"status": "ok", "db": "ok", "version": sha}:
                    print(f"{owner}: own database ready at {sha}", flush=True)
                    return
        except (URLError, TimeoutError, ValueError):
            pass
        time.sleep(5)
    raise RuntimeError(f"{owner} did not report healthy at the deployed SHA")


def main():
    manifest = yaml.safe_load((ROOT / "system.yaml").read_text(encoding="utf-8"))
    settings = manifest["azure"]
    group = settings["resource_group"]
    sha = os.environ["GITHUB_SHA"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("GITHUB_SHA must identify the tested commit")
    admin_password = os.environ["AZURE_SQL_ADMIN_PASSWORD"]
    storage = manifest["ui"]["storage_account"]
    prefix = manifest["prefix"]
    for name, value in {"PREFIX": prefix, "RESOURCE_GROUP": group, "UI_STORAGE": storage}.items():
        if os.environ.get(name) != value:
            raise ValueError(f"GitHub variable {name} must match system.yaml")
    sql_location = os.environ.get("AZURE_SQL_LOCATION", "").strip()
    foundation_parameters = {
        "prefix": prefix,
        "storageAccountName": storage,
        "sqlAdminPassword": admin_password,
    }
    if sql_location:
        foundation_parameters["sqlLocation"] = sql_location
    print("Provisioning Azure infrastructure in the student resource group", flush=True)
    foundation = provision(
        group,
        "azure-foundation",
        foundation_parameters,
        sha,
    )
    az(
        "storage",
        "blob",
        "service-properties",
        "update",
        "--account-name",
        storage,
        "--auth-mode",
        "key",
        "--static-website",
        "true",
        "--index-document",
        "index.html",
    )
    origin = az(
        "storage",
        "account",
        "show",
        "--resource-group",
        group,
        "--name",
        storage,
        "--query",
        "primaryEndpoints.web",
        output=True,
    ).rstrip("/")
    if origin != manifest["public_url"].rstrip("/"):
        raise RuntimeError("The storage website endpoint differs from system.yaml")
    server = foundation["sqlServerFqdn"]
    database_urls = {
        owner: bootstrap_sql(
            server,
            manifest["services"][owner]["azure"]["sql_database"],
            owner,
            admin_password,
            os.environ[f"{owner.upper()}_SQL_PASSWORD"],
        )
        for owner in OWNERS
    }
    registry = settings["registry"]
    print("Deploying the Rooms and Game containers", flush=True)
    apps = provision(
        group,
        "azure-apps",
        {
            "prefix": prefix,
            "uiOrigin": origin,
            "gitSha": sha,
            "roomsImage": f"{registry}/repeat-rooms:{sha}",
            "gameImage": f"{registry}/repeat-game:{sha}",
            "roomsDatabaseUrl": database_urls["rooms"],
            "gameDatabaseUrl": database_urls["game"],
            "serviceToken": os.environ["REPEAT_SERVICE_TOKEN"],
        },
        sha,
    )
    for owner in OWNERS:
        wait_health(owner, apps[f"{owner}Origin"], sha)
    origins = {f"{owner}Origin": apps[f"{owner}Origin"] for owner in OWNERS}
    publish_ui(storage, ROOT / manifest["ui"]["path"], origins)
    with urlopen(origin + "/config.js", timeout=8) as response:
        actual = response.read().decode("utf-8")
        if actual != "window.REPEAT_CONFIG = " + json.dumps(origins) + ";\n":
            raise RuntimeError(
                "The deployed frontend did not serve the tested API origins"
            )
    print(f"UI ready at {origin}/", flush=True)


if __name__ == "__main__":
    main()
