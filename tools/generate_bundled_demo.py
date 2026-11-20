"""Render the original, sample-free soundtrack for the split service Demo."""

import hashlib
import io
import json
import math
import random
import subprocess
import sys
import wave
from array import array
from pathlib import Path

PACK = Path(__file__).resolve().parents[1] / "catalog/bundled_demo"
RATE = 24000
TITLES = (
    "Paper Satellites",
    "Orange Platform",
    "Little Dynamo",
    "Porcelain Sky",
    "Glass Orchard",
    "Second Elevator",
    "Pale Voltage",
    "Midnight Handrail",
    "Blueberry Arcade",
    "Soft Compass",
    "Rain on Copper",
    "Quiet Conveyor",
    "Tin Horizon",
    "Sunday Relay",
    "Magnet Garden",
    "Neon Umbrella",
    "Hollow Lantern",
    "Juniper Switch",
    "Lemon Metro",
    "Distant Signal",
    "Clockwork Coast",
    "Amber Shortcut",
    "Rubber Comet",
    "Low Tide Letters",
    "Mint Frequency",
    "Velvet Traffic",
    "Roofline Echo",
    "Silver Sidewalk",
    "Woven Static",
    "Dusky Terminal",
    "Apricot Motor",
    "Plastic Meadow",
    "Moss Circuit",
    "Cloud Receipt",
    "Indigo Pocket",
    "Cedar Spiral",
    "Marble Radio",
    "After the Ferry",
    "Violet Crosswalk",
    "Half a Lighthouse",
    "Spare Orbit",
    "Hidden Landing",
    "Snow Telescope",
    "Copper Footnote",
    "Green Hourglass",
    "Window Tunnel",
    "Frosted Carousel",
    "Empty Teacup",
)
PERSONAL_COUNT = 40
ARTISTS = ("Repeat Pulse", "Repeat Keys", "Repeat Drift")
PROGRESSIONS = ((0, 5, 7, 0), (0, -4, 5, 7), (0, 3, -2, 5), (0, 7, 2, 5))


def note(samples, start, duration, pitch, volume, brightness):
    offset = int(start * RATE)
    count = min(int(duration * RATE), len(samples) - offset)
    frequency = 440 * 2 ** ((pitch - 69) / 12)
    step = math.tau * frequency / RATE
    for index in range(count):
        phase = step * index
        envelope = min(1, index / 180, (count - index) / 1600)
        decay = math.exp(-1.8 * index / count)
        samples[offset + index] += (
            volume
            * envelope
            * decay
            * (
                math.sin(phase)
                + brightness * math.sin(2 * phase)
                + brightness * 0.3 * math.sin(3 * phase)
            )
        )


def percussion(samples, start, cue, rng):
    offset = int(start * RATE)
    count = min(RATE // 6, len(samples) - offset)
    for index in range(count):
        time = index / RATE
        envelope = min(1, index / 36) * math.exp(-40 * time)
        if cue == "kick":
            value = 0.32 * math.sin(math.tau * (49 * time + 1 - math.exp(-47 * time)))
        else:
            value = (0.22 if cue == "clap" else 0.10) * rng.uniform(-1, 1)
        samples[offset + index] += envelope * value


def render(index):
    rng = random.Random(9271 + index)
    beat = 60 / rng.randint(94, 138)
    root = 48 + rng.randrange(12)
    minor = index % 3 == 2
    scale = (0, 2, 3, 5, 7, 10, 12) if minor else (0, 2, 4, 5, 7, 9, 12)
    motif = rng.sample(scale, 5)
    accents = rng.choice(((0, 0.75, 1.5, 2.5, 3.25), (0, 1, 1.75, 2.75, 3.5)))
    progression = PROGRESSIONS[index % len(PROGRESSIONS)]
    samples = [0.0] * int(beat * 16 * RATE)
    for bar, chord in enumerate(progression):
        for pulse in (0, 1.5, 2, 3):
            note(
                samples,
                (bar * 4 + pulse) * beat,
                beat * 0.55,
                root + chord - 12,
                0.27,
                0.16,
            )
        for interval in (0, 3 if minor else 4, 7):
            note(
                samples,
                bar * 4 * beat,
                beat * 3.8,
                root + chord + interval,
                0.065,
                0.15,
            )
        for position, accent in enumerate(accents):
            degree = motif[position if bar % 2 == 0 else (position + 2) % len(motif)]
            note(
                samples,
                (bar * 4 + accent) * beat,
                beat * (0.65 if position == 0 else 0.39),
                root + chord + degree + 12,
                0.32,
                0.12 + (index % 3) * 0.21,
            )
        for pulse, cue in (
            (0, "kick"),
            (1, "clap"),
            (1.5, "hat"),
            (2, "kick"),
            (3, "clap"),
            (3.5, "hat"),
        ):
            percussion(samples, (bar * 4 + pulse) * beat, cue, rng)
    gain = 23000 / max(abs(item) for item in samples)
    data = array("h", (round(item * gain) for item in samples))
    if sys.byteorder == "big":
        data.byteswap()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        stream.writeframes(data.tobytes())
    return buffer.getvalue()


def main():
    destination = PACK / "clips"
    destination.mkdir(parents=True, exist_ok=True)
    entries = []
    for index, title in enumerate(TITLES):
        slug = "-".join(title.lower().split())
        path = destination / f"{slug}.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "wav",
                "-i",
                "pipe:0",
                "-map_metadata",
                "-1",
                "-id3v2_version",
                "0",
                "-write_xing",
                "0",
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "40k",
                str(path),
            ],
            input=render(index),
            check=True,
        )
        artist = ARTISTS[index % len(ARTISTS)]
        entries.append(
            {
                "id": slug,
                "title": title,
                "artist": artist,
                "artists": [
                    {
                        "artist_key": "demo:" + artist.lower().replace(" ", "-"),
                        "name": artist,
                    }
                ],
                "pool_kind": "personal" if index < PERSONAL_COUNT else "decoy",
                "preview_url": f"/static/demo/local/bundled-v1/{path.name}",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    (PACK / "demo_catalog.json").write_text(
        json.dumps(entries, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Rendered {len(entries)} original Demo clips")


if __name__ == "__main__":
    main()
