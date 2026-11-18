CREATE TABLE room_start_operations (
    id VARCHAR(32) COLLATE Latin1_General_100_BIN2 PRIMARY KEY,
    room_id VARCHAR(32) COLLATE Latin1_General_100_BIN2 NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    request_sha256 VARCHAR(64) COLLATE Latin1_General_100_BIN2 NOT NULL,
    payload_json NVARCHAR(MAX) NOT NULL CHECK (ISJSON(payload_json,OBJECT)=1),
    created_at_ms BIGINT NOT NULL,
    game_id VARCHAR(32) COLLATE Latin1_General_100_BIN2 NULL,
    UNIQUE (room_id,request_sha256)
);
CREATE UNIQUE INDEX one_pending_room_start ON room_start_operations(room_id) WHERE game_id IS NULL;
