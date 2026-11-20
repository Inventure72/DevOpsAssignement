ALTER TABLE room_start_operations ADD reconciled_at_ms BIGINT NULL;
EXEC('CREATE UNIQUE INDEX one_unreconciled_room_start ON room_start_operations(room_id) WHERE reconciled_at_ms IS NULL');
