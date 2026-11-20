CREATE TABLE room_start_operations (
    id TEXT PRIMARY KEY NOT NULL,
    room_id TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    request_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json) AND json_type(payload_json)='object'),
    created_at_ms INTEGER NOT NULL,
    game_id TEXT,
    UNIQUE (room_id,request_sha256)
);
CREATE UNIQUE INDEX one_pending_room_start ON room_start_operations(room_id) WHERE game_id IS NULL;
