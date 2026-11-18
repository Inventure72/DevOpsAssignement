CREATE TABLE games (
    storage_order BIGINT IDENTITY(1,1) NOT NULL,
    id VARCHAR(32) COLLATE Latin1_General_100_BIN2 PRIMARY KEY NOT NULL,
    room_id NVARCHAR(256) COLLATE Latin1_General_100_BIN2 NOT NULL,
    room_revision BIGINT NOT NULL CHECK (room_revision >= 0),
    start_request_id NVARCHAR(256) COLLATE Latin1_General_100_BIN2 NOT NULL,
    state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    status VARCHAR(24) COLLATE Latin1_General_100_BIN2 NOT NULL CHECK (status IN ('preparing', 'playing', 'completed', 'aborted')),
    phase VARCHAR(24) COLLATE Latin1_General_100_BIN2 NOT NULL CHECK (phase IN ('setup', 'ready', 'countdown', 'answering', 'reveal', 'leaderboard', 'finished')),
    settings_json NVARCHAR(MAX) NOT NULL CHECK (ISJSON(settings_json, OBJECT)=1),
    songs_snapshot_json NVARCHAR(MAX) NOT NULL CHECK (ISJSON(songs_snapshot_json, ARRAY)=1),
    round_plan_json NVARCHAR(MAX) NOT NULL DEFAULT '[]' CHECK (ISJSON(round_plan_json, ARRAY)=1),
    started_at_ms BIGINT NOT NULL,
    prepared_at_ms BIGINT,
    phase_ends_at_ms BIGINT,
    end_reason NVARCHAR(MAX),
    ended_at_ms BIGINT,
    room_id_bytes AS DATALENGTH(room_id) PERSISTED NOT NULL,
    start_request_bytes AS DATALENGTH(start_request_id) PERSISTED NOT NULL,
    UNIQUE (room_id, room_id_bytes, start_request_id, start_request_bytes),
    CHECK (prepared_at_ms >= started_at_ms),
    CHECK (ended_at_ms >= started_at_ms),
    CHECK ((status IN ('preparing', 'playing') AND ended_at_ms IS NULL AND end_reason IS NULL AND phase != 'finished')
        OR (status IN ('completed', 'aborted') AND ended_at_ms IS NOT NULL AND end_reason IS NOT NULL AND phase = 'finished' AND phase_ends_at_ms IS NULL)),
    CHECK (status != 'preparing' OR phase IN ('setup', 'ready')),
    CHECK (status != 'playing' OR phase IN ('ready', 'countdown', 'answering', 'reveal', 'leaderboard')),
    CHECK (status NOT IN ('playing', 'completed') OR prepared_at_ms IS NOT NULL),
    CHECK (phase NOT IN ('setup', 'countdown', 'reveal', 'leaderboard') OR phase_ends_at_ms IS NOT NULL),
    CHECK (phase_ends_at_ms IS NULL OR phase_ends_at_ms >= started_at_ms)
);

CREATE TABLE game_players (
    storage_order BIGINT IDENTITY(1,1) NOT NULL,
    game_id VARCHAR(32) COLLATE Latin1_General_100_BIN2 NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    player_id NVARCHAR(256) COLLATE Latin1_General_100_BIN2 NOT NULL,
    nickname NVARCHAR(MAX) NOT NULL,
    character_id NVARCHAR(MAX) NOT NULL,
    is_host TINYINT NOT NULL CHECK (is_host IN (0, 1)),
    final_score BIGINT CHECK (final_score >= 0),
    final_rank BIGINT CHECK (final_rank >= 1),
    PRIMARY KEY (game_id, player_id),
    CHECK ((final_score IS NULL AND final_rank IS NULL) OR (final_score IS NOT NULL AND final_rank IS NOT NULL))
);

CREATE TABLE rounds (
    id VARCHAR(32) COLLATE Latin1_General_100_BIN2 PRIMARY KEY NOT NULL,
    game_id VARCHAR(32) COLLATE Latin1_General_100_BIN2 NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    round_number BIGINT NOT NULL CHECK (round_number BETWEEN 1 AND 15),
    attempt BIGINT NOT NULL DEFAULT 1 CHECK (attempt BETWEEN 1 AND 4),
    status VARCHAR(24) COLLATE Latin1_General_100_BIN2 NOT NULL CHECK (status IN ('ready', 'playing', 'revealed', 'void')),
    song_key NVARCHAR(MAX) NOT NULL,
    difficulty VARCHAR(24) COLLATE Latin1_General_100_BIN2 NOT NULL CHECK (difficulty IN ('easy', 'medium', 'hard', 'decoy')),
    picked_player_id NVARCHAR(256) COLLATE Latin1_General_100_BIN2,
    starts_at_ms BIGINT,
    deadline_at_ms BIGINT,
    readiness_generation BIGINT NOT NULL DEFAULT 1 CHECK (readiness_generation >= 1),
    readiness_deadline_at_ms BIGINT NOT NULL,
    ready_player_ids_json NVARCHAR(MAX) NOT NULL DEFAULT '[]' CHECK (ISJSON(ready_player_ids_json, ARRAY)=1),
    excluded_player_ids_json NVARCHAR(MAX) NOT NULL DEFAULT '[]' CHECK (ISJSON(excluded_player_ids_json, ARRAY)=1),
    revealed_at_ms BIGINT,
    void_reason NVARCHAR(MAX),
    UNIQUE (game_id, id),
    UNIQUE (game_id, round_number, attempt),
    song_digest AS CONVERT(BINARY(32),HASHBYTES('SHA2_256',CONVERT(VARBINARY(MAX),song_key))) PERSISTED,
    UNIQUE (game_id, song_digest),
    FOREIGN KEY (game_id, picked_player_id) REFERENCES game_players(game_id, player_id),
    CHECK ((difficulty = 'decoy' AND picked_player_id IS NULL) OR (difficulty != 'decoy' AND picked_player_id IS NOT NULL)),
    CHECK ((starts_at_ms IS NULL AND deadline_at_ms IS NULL) OR (starts_at_ms IS NOT NULL AND deadline_at_ms IS NOT NULL AND deadline_at_ms > starts_at_ms)),
    CHECK (status NOT IN ('playing', 'revealed') OR starts_at_ms IS NOT NULL),
    CHECK ((status = 'revealed' AND revealed_at_ms IS NOT NULL AND revealed_at_ms >= starts_at_ms) OR (status != 'revealed' AND revealed_at_ms IS NULL)),
    CHECK ((status = 'void' AND void_reason IS NOT NULL) OR (status != 'void' AND void_reason IS NULL))
);

CREATE TABLE answers (
    game_id VARCHAR(32) COLLATE Latin1_General_100_BIN2 NOT NULL,
    round_id VARCHAR(32) COLLATE Latin1_General_100_BIN2 NOT NULL,
    player_id NVARCHAR(256) COLLATE Latin1_General_100_BIN2 NOT NULL,
    status VARCHAR(24) COLLATE Latin1_General_100_BIN2 NOT NULL CHECK (status IN ('submitted', 'missing')),
    song_guess_json NVARCHAR(MAX) NOT NULL DEFAULT 'null' CHECK (ISJSON(song_guess_json,OBJECT)=1 OR TRIM(song_guess_json)='null'),
    who_mode VARCHAR(24) COLLATE Latin1_General_100_BIN2 NOT NULL CHECK (who_mode IN ('blank', 'nobody', 'players')),
    who_player_ids_json NVARCHAR(MAX) NOT NULL DEFAULT '[]' CHECK (ISJSON(who_player_ids_json, ARRAY)=1),
    received_at_ms BIGINT,
    points BIGINT CHECK (points >= 0),
    PRIMARY KEY (round_id, player_id),
    FOREIGN KEY (game_id, round_id) REFERENCES rounds(game_id, id) ON DELETE CASCADE,
    FOREIGN KEY (game_id, player_id) REFERENCES game_players(game_id, player_id),
    CHECK ((who_mode = 'players' AND JSON_PATH_EXISTS(who_player_ids_json,'$[0]')=1) OR (who_mode != 'players' AND JSON_PATH_EXISTS(who_player_ids_json,'$[0]')=0)),
    CHECK ((status = 'submitted' AND received_at_ms IS NOT NULL AND who_mode IN ('nobody', 'players')) OR (status = 'missing' AND received_at_ms IS NULL AND TRIM(song_guess_json)='null' AND who_mode = 'blank' AND points IS NOT NULL AND points = 0))
);

CREATE TABLE game_commands (
    game_id VARCHAR(32) COLLATE Latin1_General_100_BIN2 NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    request_id NVARCHAR(256) COLLATE Latin1_General_100_BIN2 NOT NULL,
    actor_player_id NVARCHAR(256) COLLATE Latin1_General_100_BIN2 NOT NULL,
    command_type VARCHAR(24) COLLATE Latin1_General_100_BIN2 NOT NULL CHECK (command_type IN ('start', 'end', 'preload_check', 'retry', 'continue', 'audio_failure', 'leave')),
    payload_json NVARCHAR(MAX) NOT NULL CHECK (ISJSON(payload_json, OBJECT)=1),
    result_json NVARCHAR(MAX) NOT NULL CHECK (ISJSON(result_json, OBJECT)=1),
    accepted_at_ms BIGINT NOT NULL,
    request_id_bytes AS DATALENGTH(request_id) PERSISTED NOT NULL,
    PRIMARY KEY (game_id, request_id, request_id_bytes),
    FOREIGN KEY (game_id, actor_player_id) REFERENCES game_players(game_id, player_id)
);

CREATE TABLE game_round_preparations (
    game_id VARCHAR(32) COLLATE Latin1_General_100_BIN2 PRIMARY KEY NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    id VARCHAR(32) COLLATE Latin1_General_100_BIN2 UNIQUE NOT NULL,
    round_number BIGINT NOT NULL CHECK (round_number BETWEEN 1 AND 15),
    attempt BIGINT NOT NULL CHECK (attempt BETWEEN 1 AND 4),
    song_key NVARCHAR(MAX) NOT NULL,
    difficulty VARCHAR(24) COLLATE Latin1_General_100_BIN2 NOT NULL CHECK (difficulty IN ('easy', 'medium', 'hard', 'decoy')),
    picked_player_id NVARCHAR(256) COLLATE Latin1_General_100_BIN2,
    readiness_generation BIGINT NOT NULL CHECK (readiness_generation >= 1),
    acknowledgements_json NVARCHAR(MAX) NOT NULL DEFAULT '{}' CHECK (
        ISJSON(acknowledgements_json, OBJECT)=1
    ),
    FOREIGN KEY (game_id, picked_player_id) REFERENCES game_players(game_id, player_id),
    CHECK ((difficulty = 'decoy' AND picked_player_id IS NULL)
        OR (difficulty != 'decoy' AND picked_player_id IS NOT NULL))
);

CREATE INDEX games_by_room ON games(room_id);

CREATE UNIQUE INDEX one_active_game ON games(room_id,room_id_bytes) WHERE status IN ('preparing', 'playing');

CREATE UNIQUE INDEX one_active_round ON rounds(game_id) WHERE status IN ('ready','playing');

CREATE UNIQUE INDEX one_game_host ON game_players(game_id) WHERE is_host = 1;

CREATE TABLE health_probe (id VARCHAR(32) PRIMARY KEY);
