-- Apply after schema-v2.5.0.sql to reproduce the synthetic schema
-- preceding the journal migration.
ALTER TABLE import_jobs ADD COLUMN error_code TEXT;
ALTER TABLE import_jobs ADD COLUMN diagnostic_json TEXT;
CREATE TABLE profile_rank_observations (
    owner_puuid TEXT NOT NULL,
    platform_region TEXT NOT NULL,
    queue_id INTEGER NOT NULL CHECK(queue_id IN (420, 440)),
    observed_at REAL NOT NULL CHECK(observed_at >= 0 AND observed_at <= 253370764800),
    status TEXT NOT NULL CHECK(status IN ('ranked', 'unranked', 'unavailable')),
    tier TEXT,
    division TEXT,
    league_points INTEGER,
    source TEXT NOT NULL DEFAULT 'league-v4' CHECK(source IN ('league-v4', 'synthetic-demo')),
    job_id TEXT UNIQUE,
    PRIMARY KEY(owner_puuid, platform_region, queue_id, observed_at),
    FOREIGN KEY(owner_puuid) REFERENCES players(puuid) ON DELETE CASCADE,
    FOREIGN KEY(job_id) REFERENCES import_jobs(job_id) ON DELETE SET NULL
);
CREATE TABLE item_catalogs (
    patch TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    schema_version INTEGER NOT NULL,
    catalog_json TEXT NOT NULL
);
CREATE INDEX idx_import_jobs_status_updated ON import_jobs(status, updated_at);
