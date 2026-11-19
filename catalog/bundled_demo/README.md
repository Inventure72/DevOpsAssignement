# Bundled Demo clips

The standalone Rooms service ships 48 short original instrumental loops:
40 for simulated listening libraries and 8 for Nobody rounds. The composition
and synthesis are in [the generator](../../tools/generate_bundled_demo.py).
It uses oscillators and procedural percussion, with no samples, provider
previews, or existing recordings. The titles and artist names are fictional
labels for the Demo. The Apple preview pack is separate and is not packaged in
the Rooms image.

The [catalog](demo_catalog.json) maps each clip to its title, artist, pool and
SHA-256 digest. Startup checks the bundled files against those digests and
seeds the standalone Rooms database. Existing room songs and Game snapshots
are preserved. Rooms assigns each player 24 distinct personal tracks and
adds the Nobody pool when the host creates the room.

The clips are approximately 7–11 seconds of mono MP3. They are served from
`/static/demo/local/bundled-v1/` on the Rooms origin. The committed files are
ready to use; Compose never invokes the generator. To regenerate for development,
run `python -m tools.generate_bundled_demo` from the repository root with FFmpeg
and its LAME encoder available. The committed clips were encoded with FFmpeg
8.1. Treat the catalog and clips as one change if regenerating them.
