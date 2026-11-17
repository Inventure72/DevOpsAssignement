-- Development SQLite schema only; SQL Server port follows in ADR-7.

CREATE TABLE rooms (
    id TEXT PRIMARY KEY NOT NULL,
    code TEXT NOT NULL UNIQUE CHECK (length(code) = 6),
    mode TEXT NOT NULL CHECK (mode IN ('normal', 'demo')),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    state TEXT NOT NULL DEFAULT 'lobby' CHECK (state IN ('lobby', 'playing')),
    created_at_ms INTEGER NOT NULL,
    last_completed_at_ms INTEGER CHECK (last_completed_at_ms >= created_at_ms)
);

CREATE TABLE players (
    id TEXT PRIMARY KEY NOT NULL,
    room_id TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    nickname TEXT NOT NULL,
    nickname_key TEXT NOT NULL,
    character_id TEXT NOT NULL,
    music_status TEXT NOT NULL CHECK (music_status IN ('authorized', 'ready', 'failed', 'demo')),
    music_provider TEXT,
    session_token_hash TEXT NOT NULL UNIQUE,
    is_host INTEGER NOT NULL CHECK (is_host IN (0, 1)),
    joined_at_ms INTEGER NOT NULL,
    last_seen_at_ms INTEGER NOT NULL, music_account_hash TEXT, shared_music_account INTEGER NOT NULL DEFAULT 0
    CHECK (shared_music_account IN (0, 1)),
    CHECK ((music_status = 'demo' AND music_provider IS NULL) OR (music_status != 'demo' AND music_provider IS NOT NULL)),
    UNIQUE (room_id, id),
    UNIQUE (room_id, nickname_key)
);

CREATE TABLE songs (
    id TEXT PRIMARY KEY NOT NULL,
    room_id TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    identity_key TEXT NOT NULL,
    isrc TEXT,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    artists_json TEXT NOT NULL CHECK (json_valid(artists_json) AND json_type(artists_json) = 'array' AND json_array_length(artists_json) >= 1),
    preview_url TEXT,
    artwork_url TEXT, pool_kind TEXT NOT NULL DEFAULT 'personal'
    CHECK (pool_kind IN ('personal', 'decoy')),
    UNIQUE (room_id, id),
    UNIQUE (room_id, identity_key)
);

CREATE TABLE player_songs (
    room_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    song_id TEXT NOT NULL,
    familiarity TEXT NOT NULL CHECK (familiarity IN ('easy', 'medium', 'hard')),
    PRIMARY KEY (player_id, song_id),
    FOREIGN KEY (room_id, player_id) REFERENCES players(room_id, id) ON DELETE CASCADE,
    FOREIGN KEY (room_id, song_id) REFERENCES songs(room_id, id) ON DELETE CASCADE
);

CREATE TABLE demo_catalog (
    id TEXT PRIMARY KEY NOT NULL,
    pool_kind TEXT NOT NULL CHECK (pool_kind IN ('personal', 'decoy')),
    isrc TEXT,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    artists_json TEXT NOT NULL CHECK (json_valid(artists_json) AND json_type(artists_json) = 'array' AND json_array_length(artists_json) >= 1),
    preview_url TEXT NOT NULL,
    artwork_url TEXT
);

CREATE UNIQUE INDEX distinct_room_music_accounts
    ON players(room_id, music_provider, music_account_hash)
    WHERE music_account_hash IS NOT NULL AND shared_music_account = 0;

CREATE UNIQUE INDEX one_room_host ON players(room_id) WHERE is_host = 1;
