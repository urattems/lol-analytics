-- Exact SCHEMA from v2.5.0 core/db.py, commit f24c83f10815c84b6037da6fbab7e0b3ed90734e.
CREATE TABLE IF NOT EXISTS players (
    puuid TEXT PRIMARY KEY,
    game_name TEXT NOT NULL,
    tag_line TEXT NOT NULL,
    region TEXT,
    platform_region TEXT,
    routing_region TEXT
);

CREATE TABLE IF NOT EXISTS matches (
    match_id TEXT PRIMARY KEY,
    patch TEXT,
    queue_id INTEGER,
    duration INTEGER,
    game_creation INTEGER
);

CREATE TABLE IF NOT EXISTS participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT NOT NULL,
    puuid TEXT NOT NULL,
    teammate_game_name TEXT,
    teammate_tag_line TEXT,
    champion TEXT,
    role TEXT,
    win INTEGER,
    kills INTEGER,
    deaths INTEGER,
    assists INTEGER,
    gold_earned INTEGER,
    cs_total INTEGER,
    damage_dealt INTEGER,
    damage_share REAL,
    vision_score INTEGER,
    items TEXT,
    side TEXT,
    FOREIGN KEY (match_id) REFERENCES matches(match_id) ON DELETE CASCADE,
    UNIQUE (match_id, puuid)
);

CREATE TABLE IF NOT EXISTS sync_state (
    puuid TEXT PRIMARY KEY,
    last_match_id_synced TEXT,
    last_sync_at INTEGER,
    FOREIGN KEY (puuid) REFERENCES players(puuid) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS timeline_status (
    match_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    fetched_at INTEGER,
    frame_count INTEGER NOT NULL DEFAULT 0,
    event_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    FOREIGN KEY (match_id) REFERENCES matches(match_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS identity_fetch_status (
    match_id TEXT PRIMARY KEY,
    checked_at INTEGER NOT NULL,
    FOREIGN KEY (match_id) REFERENCES matches(match_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS import_jobs (
    job_id TEXT PRIMARY KEY,
    puuid TEXT,
    game_name TEXT NOT NULL,
    tag_line TEXT NOT NULL,
    platform_region TEXT NOT NULL,
    routing_region TEXT NOT NULL,
    kind TEXT NOT NULL,
    target INTEGER,
    match_id TEXT,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    local_before INTEGER NOT NULL DEFAULT 0,
    local_count INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    total INTEGER,
    phase TEXT NOT NULL DEFAULT 'preparing',
    message TEXT,
    FOREIGN KEY (puuid) REFERENCES players(puuid) ON DELETE CASCADE,
    FOREIGN KEY (match_id) REFERENCES matches(match_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS import_cooldown (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    not_before REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS import_job_pages (
    job_id TEXT NOT NULL,
    request_key TEXT NOT NULL,
    match_ids_json TEXT NOT NULL,
    PRIMARY KEY(job_id, request_key),
    FOREIGN KEY (job_id) REFERENCES import_jobs(job_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rank_snapshots (
    match_id TEXT NOT NULL,
    puuid TEXT NOT NULL,
    queue_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    tier TEXT,
    division TEXT,
    league_points INTEGER,
    fetched_at REAL NOT NULL,
    PRIMARY KEY(match_id, puuid),
    FOREIGN KEY(match_id, puuid) REFERENCES participants(match_id, puuid) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS timeline_frames (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT NOT NULL,
    participant_id INTEGER NOT NULL,
    puuid TEXT,
    timestamp_ms INTEGER NOT NULL,
    total_gold INTEGER,
    current_gold INTEGER,
    xp INTEGER,
    level INTEGER,
    minions_killed INTEGER,
    jungle_minions_killed INTEGER,
    cs_total INTEGER,
    position_x INTEGER,
    position_y INTEGER,
    FOREIGN KEY (match_id) REFERENCES matches(match_id) ON DELETE CASCADE,
    UNIQUE (match_id, participant_id, timestamp_ms)
);

CREATE TABLE IF NOT EXISTS timeline_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT NOT NULL,
    event_index INTEGER NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    participant_id INTEGER,
    killer_id INTEGER,
    victim_id INTEGER,
    creator_id INTEGER,
    item_id INTEGER,
    item_before_id INTEGER,
    item_after_id INTEGER,
    team_id INTEGER,
    monster_type TEXT,
    monster_subtype TEXT,
    building_type TEXT,
    tower_type TEXT,
    lane_type TEXT,
    extra_json TEXT,
    FOREIGN KEY (match_id) REFERENCES matches(match_id) ON DELETE CASCADE,
    UNIQUE (match_id, event_index)
);

CREATE INDEX IF NOT EXISTS idx_matches_creation ON matches(game_creation DESC);
CREATE INDEX IF NOT EXISTS idx_participants_puuid ON participants(puuid);
CREATE INDEX IF NOT EXISTS idx_participants_match_id ON participants(match_id);
CREATE INDEX IF NOT EXISTS idx_participants_champion ON participants(champion);
CREATE INDEX IF NOT EXISTS idx_timeline_frames_match ON timeline_frames(match_id);
CREATE INDEX IF NOT EXISTS idx_timeline_frames_match_player_time ON timeline_frames(match_id, puuid, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_timeline_frames_puuid_time ON timeline_frames(puuid, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_timeline_events_match_time ON timeline_events(match_id, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_timeline_events_type ON timeline_events(event_type);
