-- Materialize elapsed time for existing and future finished games.
-- Cast before subtracting so the full accepted BIGINT timestamp range fits.
ALTER TABLE dbo.games ADD duration_ms AS (
    CONVERT(DECIMAL(19, 0), ended_at_ms) - CONVERT(DECIMAL(19, 0), started_at_ms)
) PERSISTED;
