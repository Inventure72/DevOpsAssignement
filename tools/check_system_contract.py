"""Validate system.yaml with the official schema and check its OpenAPI links."""

import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_HEADER = "# yaml-language-server: $schema=./system.schema.json"


def check_system_contract(root: Path = ROOT):
    manifest = (root / "system.yaml").read_text(encoding="utf-8")
    if manifest.splitlines()[:1] != [SCHEMA_HEADER]:
        raise ValueError("system.yaml must start with the assignment schema header")
    schema = json.loads((root / "system.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    document = yaml.safe_load(manifest)
    errors = Draft202012Validator(schema).iter_errors(document)
    invalid = sorted({f"{error.json_path} ({error.validator})" for error in errors})
    if invalid:
        raise ValueError("system.yaml schema violations: " + ", ".join(invalid))

    services = document["services"]
    for name in document["ui"]["calls"]:
        if name not in services:
            raise ValueError(f"UI refers to an undeclared service: {name}")
    operations = {}
    for name, service in services.items():
        openapi = json.loads((root / service["openapi"]).read_text(encoding="utf-8"))
        operations[name] = {
            f"{method.upper()} {path}"
            for path, item in openapi["paths"].items()
            for method in ("get", "post", "put", "patch", "delete")
            if method in item
        }
    for name, service in services.items():
        for call in service.get("calls", []):
            target = call["service"]
            if target not in operations:
                raise ValueError(f"{name} refers to an undeclared service: {target}")
            if call["url_env"] not in service["env"]:
                raise ValueError(
                    f"{name} has an undeclared peer URL environment variable"
                )
            unlisted = set(call["endpoints"]) - operations[target]
            if unlisted:
                raise ValueError(
                    f"{name} calls endpoints missing from {target} OpenAPI: "
                    + ", ".join(sorted(unlisted))
                )


def main():
    try:
        check_system_contract()
    except yaml.YAMLError:
        raise SystemExit("system.yaml contains invalid YAML") from None
    except SchemaError:
        raise SystemExit(
            "system.schema.json is not a valid Draft 2020-12 schema"
        ) from None
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    print("system.yaml: assignment schema and declared OpenAPI calls valid")


if __name__ == "__main__":
    main()
