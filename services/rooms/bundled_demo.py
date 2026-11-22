"""Original Demo pack and server-owned listening assignments."""

import hashlib
import json
import re
from pathlib import Path
from secrets import SystemRandom

from services.rooms.domain.demo import catalog_snapshot, seed_demo
from shared.errors import DomainError

PACK = Path(__file__).resolve().parents[2] / "catalog/bundled_demo"
CATALOG = PACK / "demo_catalog.json"
CLIPS = PACK / "clips"
MEDIA_PATH = "/static/demo/local/bundled-v1"
PLAYLIST_SIZE = 24


def validate_media():
    entries = json.loads(CATALOG.read_text(encoding="utf-8"))
    clip_hashes = set()
    counts = {"personal": 0, "decoy": 0}
    for entry in entries:
        slug = entry["id"]
        name = f"{slug}.mp3"
        if (
            not re.fullmatch(r"[a-z0-9-]+", slug)
            or entry["preview_url"] != f"{MEDIA_PATH}/{name}"
        ):
            raise ValueError("Bundled Demo contains an invalid clip path")
        data = (CLIPS / name).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if not data or digest != entry["sha256"] or digest in clip_hashes:
            raise ValueError(f"Bundled Demo contains an invalid clip: {name}")
        clip_hashes.add(digest)
        counts[entry["pool_kind"]] += 1
    if counts["personal"] < PLAYLIST_SIZE or counts["decoy"] < 4:
        raise ValueError("Bundled Demo does not have enough distinct clips")


def initialize(db):
    validate_media()
    with db.transaction() as conn:
        seed_demo(conn, CATALOG)


def listening_import(conn, *, include_decoys=False):
    pools = catalog_snapshot(conn)
    if len(pools["personal"]) < PLAYLIST_SIZE:
        raise DomainError("demo_unavailable", "Demo music is not available.", 503)
    songs = SystemRandom().sample(pools["personal"], PLAYLIST_SIZE)
    return {
        "provider": "demo",
        "evidence": "simulated",
        "songs": [
            song
            | {
                "familiarity": "easy"
                if index < 12
                else "medium"
                if index < 20
                else "hard"
            }
            for index, song in enumerate(songs)
        ],
        "decoys": pools["decoy"] if include_decoys else [],
    }
