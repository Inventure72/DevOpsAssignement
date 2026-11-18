-- SQL Server 2022 / Azure SQL. Forward-only; never edit an applied migration.
CREATE TABLE rooms (
    id VARCHAR(32) COLLATE Latin1_General_100_BIN2 PRIMARY KEY,
    code VARCHAR(6) COLLATE Latin1_General_100_BIN2 NOT NULL UNIQUE CHECK (DATALENGTH(code)=6),
    mode VARCHAR(10) COLLATE Latin1_General_100_BIN2 NOT NULL CHECK (mode IN ('normal','demo') AND DATALENGTH(mode)=LEN(mode)),
    revision BIGINT NOT NULL DEFAULT 0 CHECK (revision>=0),
    state VARCHAR(10) COLLATE Latin1_General_100_BIN2 NOT NULL DEFAULT 'lobby' CHECK (state IN ('lobby','playing') AND DATALENGTH(state)=LEN(state)),
    created_at_ms BIGINT NOT NULL,
    last_completed_at_ms BIGINT NULL,
    CHECK (last_completed_at_ms>=created_at_ms)
);
CREATE TABLE players (
    id VARCHAR(32) COLLATE Latin1_General_100_BIN2 PRIMARY KEY,
    room_id VARCHAR(32) COLLATE Latin1_General_100_BIN2 NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    nickname NVARCHAR(48) NOT NULL,
    nickname_key NVARCHAR(144) COLLATE Latin1_General_100_BIN2 NOT NULL,
    character_id VARCHAR(20) NOT NULL,
    music_status VARCHAR(20) COLLATE Latin1_General_100_BIN2 NOT NULL CHECK (music_status IN ('authorized','ready','failed','demo') AND DATALENGTH(music_status)=LEN(music_status)),
    music_provider VARCHAR(32) COLLATE Latin1_General_100_BIN2 NULL,
    session_token_hash VARCHAR(64) COLLATE Latin1_General_100_BIN2 NOT NULL UNIQUE,
    is_host TINYINT NOT NULL CHECK (is_host IN (0,1)),
    joined_at_ms BIGINT NOT NULL,
    last_seen_at_ms BIGINT NOT NULL,
    music_account_hash VARCHAR(64) COLLATE Latin1_General_100_BIN2 NULL,
    shared_music_account TINYINT NOT NULL DEFAULT 0 CHECK (shared_music_account IN (0,1)),
    CHECK ((music_status='demo' AND music_provider IS NULL) OR (music_status<>'demo' AND music_provider IS NOT NULL)),
    UNIQUE (room_id,id),
    UNIQUE (room_id,nickname_key)
);
CREATE UNIQUE INDEX one_room_host ON players(room_id) WHERE is_host=1;
CREATE UNIQUE INDEX distinct_room_music_accounts ON players(room_id,music_provider,music_account_hash)
    WHERE music_account_hash IS NOT NULL AND shared_music_account=0;
CREATE TABLE songs (
    id VARCHAR(32) COLLATE Latin1_General_100_BIN2 PRIMARY KEY,
    room_id VARCHAR(32) COLLATE Latin1_General_100_BIN2 NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    identity_key NVARCHAR(MAX) COLLATE Latin1_General_100_BIN2 NOT NULL,
    identity_digest AS CONVERT(BINARY(32),HASHBYTES('SHA2_256',CONVERT(VARBINARY(MAX),identity_key))) PERSISTED,
    isrc NVARCHAR(MAX) NULL,
    title NVARCHAR(MAX) NOT NULL,
    artist NVARCHAR(MAX) NOT NULL,
    artists_json NVARCHAR(MAX) NOT NULL CHECK (ISJSON(artists_json,ARRAY)=1 AND JSON_PATH_EXISTS(artists_json,'$[0]')=1),
    preview_url NVARCHAR(MAX) NULL,
    artwork_url NVARCHAR(MAX) NULL,
    pool_kind VARCHAR(10) COLLATE Latin1_General_100_BIN2 NOT NULL DEFAULT 'personal' CHECK (pool_kind IN ('personal','decoy') AND DATALENGTH(pool_kind)=LEN(pool_kind)),
    UNIQUE (room_id,id),
    UNIQUE (room_id,identity_digest)
);
CREATE TABLE player_songs (
    room_id VARCHAR(32) COLLATE Latin1_General_100_BIN2 NOT NULL,
    player_id VARCHAR(32) COLLATE Latin1_General_100_BIN2 NOT NULL,
    song_id VARCHAR(32) COLLATE Latin1_General_100_BIN2 NOT NULL,
    familiarity VARCHAR(10) COLLATE Latin1_General_100_BIN2 NOT NULL CHECK (familiarity IN ('easy','medium','hard') AND DATALENGTH(familiarity)=LEN(familiarity)),
    PRIMARY KEY (player_id,song_id),
    FOREIGN KEY (room_id,player_id) REFERENCES players(room_id,id) ON DELETE CASCADE,
    -- Two cascading paths from rooms are forbidden by SQL Server. Room deletion
    -- removes memberships first in the same application transaction.
    FOREIGN KEY (room_id,song_id) REFERENCES songs(room_id,id) ON DELETE NO ACTION
);
CREATE TABLE demo_catalog (
    id NVARCHAR(MAX) COLLATE Latin1_General_100_BIN2 NOT NULL,
    id_digest AS CONVERT(BINARY(32),HASHBYTES('SHA2_256',CONVERT(VARBINARY(MAX),id))) PERSISTED NOT NULL,
    pool_kind VARCHAR(10) COLLATE Latin1_General_100_BIN2 NOT NULL CHECK (pool_kind IN ('personal','decoy') AND DATALENGTH(pool_kind)=LEN(pool_kind)),
    isrc NVARCHAR(MAX) NULL,
    title NVARCHAR(MAX) NOT NULL,
    artist NVARCHAR(MAX) NOT NULL,
    artists_json NVARCHAR(MAX) NOT NULL CHECK (ISJSON(artists_json,ARRAY)=1 AND JSON_PATH_EXISTS(artists_json,'$[0]')=1),
    preview_url NVARCHAR(MAX) NOT NULL,
    artwork_url NVARCHAR(MAX) NULL,
    PRIMARY KEY (id_digest)
);
CREATE TABLE health_probe (id VARCHAR(32) PRIMARY KEY);
