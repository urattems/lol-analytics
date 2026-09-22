"""Small SQLite persistence layer with idempotent match insertion."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from core.models import ParsedMatch, RiotAccount


SCHEMA = """
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
    error_code TEXT,
    diagnostic_json TEXT,
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

CREATE TABLE IF NOT EXISTS profile_rank_observations (
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

CREATE TABLE IF NOT EXISTS item_catalogs (
    patch TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    schema_version INTEGER NOT NULL,
    catalog_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS match_notes (
    owner_puuid TEXT NOT NULL REFERENCES players(puuid) ON DELETE CASCADE,
    match_id TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '' CHECK(length(body) <= 5000),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    updated_at REAL NOT NULL CHECK(updated_at >= 0 AND updated_at <= 253370764800),
    PRIMARY KEY(owner_puuid, match_id),
    FOREIGN KEY(match_id, owner_puuid) REFERENCES participants(match_id, puuid) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS match_tags (
    owner_puuid TEXT NOT NULL,
    match_id TEXT NOT NULL,
    tag TEXT NOT NULL CHECK(length(tag) BETWEEN 1 AND 40),
    PRIMARY KEY(owner_puuid, match_id, tag),
    FOREIGN KEY(owner_puuid, match_id) REFERENCES match_notes(owner_puuid, match_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS personal_goals (
    goal_id TEXT PRIMARY KEY,
    owner_puuid TEXT NOT NULL REFERENCES players(puuid) ON DELETE CASCADE,
    title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 120),
    metric_key TEXT CHECK(metric_key IN ('cs_per_minute','deaths_per_game','gold_diff_15','vision_per_minute')),
    comparator TEXT CHECK(comparator IN ('gte','lte')),
    target_value REAL,
    horizon INTEGER CHECK(horizon IN (5,10)),
    champion TEXT, role TEXT, queue_id INTEGER,
    patch_policy TEXT NOT NULL CHECK(patch_policy IN ('specific','mixed')),
    patch TEXT,
    include_short INTEGER NOT NULL DEFAULT 0 CHECK(include_short IN (0,1)),
    baseline_participant_id INTEGER NOT NULL CHECK(baseline_participant_id >= 0),
    created_at REAL NOT NULL CHECK(created_at >= 0 AND created_at <= 253370764800),
    closed_at REAL CHECK(closed_at >= created_at AND closed_at <= 253370764800),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    source TEXT NOT NULL DEFAULT 'user' CHECK(source IN ('user','synthetic-demo')),
    CHECK((metric_key IS NULL AND comparator IS NULL AND target_value IS NULL AND horizon IS NULL)
       OR (metric_key IS NOT NULL AND comparator IS NOT NULL AND target_value IS NOT NULL AND horizon IS NOT NULL)),
    CHECK(patch_policy = 'mixed' OR (patch IS NOT NULL AND length(patch) > 0))
);
CREATE INDEX IF NOT EXISTS idx_personal_goals_owner ON personal_goals(owner_puuid, created_at);

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
CREATE INDEX IF NOT EXISTS idx_import_jobs_status_updated ON import_jobs(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_participants_puuid ON participants(puuid);
CREATE INDEX IF NOT EXISTS idx_participants_match_id ON participants(match_id);
CREATE INDEX IF NOT EXISTS idx_participants_champion ON participants(champion);
CREATE INDEX IF NOT EXISTS idx_timeline_frames_match ON timeline_frames(match_id);
CREATE INDEX IF NOT EXISTS idx_timeline_frames_match_player_time ON timeline_frames(match_id, puuid, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_timeline_frames_puuid_time ON timeline_frames(puuid, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_timeline_events_match_time ON timeline_events(match_id, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_timeline_events_type ON timeline_events(event_type);
"""


@dataclass(frozen=True)
class ProfileRemovalResult:
    removed: bool
    deleted_matches: int = 0
    shared_matches: int = 0
    backup_path: Path | None = None
    backup_created_at: str | None = None

    def __bool__(self) -> bool:
        return self.removed


class Database:
    """Own database connections and parameterized storage operations."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a configured connection and always close it."""

        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create tables and apply additive, idempotent schema migrations."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            player_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(players)")
            }
            if "platform_region" not in player_columns:
                connection.execute("ALTER TABLE players ADD COLUMN platform_region TEXT")
            if "routing_region" not in player_columns:
                connection.execute("ALTER TABLE players ADD COLUMN routing_region TEXT")
            job_columns = {row["name"] for row in connection.execute("PRAGMA table_info(import_jobs)")}
            if "match_id" not in job_columns:
                connection.execute("ALTER TABLE import_jobs ADD COLUMN match_id TEXT REFERENCES matches(match_id) ON DELETE CASCADE")
            for column in ('error_code', 'diagnostic_json'):
                if column not in job_columns:
                    connection.execute(f'ALTER TABLE import_jobs ADD COLUMN {column} TEXT')
            participant_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(participants)")
            }
            if "teammate_game_name" not in participant_columns:
                connection.execute("ALTER TABLE participants ADD COLUMN teammate_game_name TEXT")
            if "teammate_tag_line" not in participant_columns:
                connection.execute("ALTER TABLE participants ADD COLUMN teammate_tag_line TEXT")
            event_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(timeline_events)")
            }
            if "item_before_id" not in event_columns:
                connection.execute("ALTER TABLE timeline_events ADD COLUMN item_before_id INTEGER")
            if "item_after_id" not in event_columns:
                connection.execute("ALTER TABLE timeline_events ADD COLUMN item_after_id INTEGER")
            self._backfill_item_undo_columns(connection)
            timeline_columns = {row['name'] for row in connection.execute('PRAGMA table_info(timeline_status)')}
            if 'dropped_item_events' not in timeline_columns:
                connection.execute('ALTER TABLE timeline_status ADD COLUMN dropped_item_events INTEGER NOT NULL DEFAULT 0')

    @staticmethod
    def _backfill_item_undo_columns(connection: sqlite3.Connection) -> None:
        """Recover ITEM_UNDO IDs already present in extra_json, idempotently."""

        rows = connection.execute(
            """
            SELECT id, extra_json FROM timeline_events
            WHERE event_type = 'ITEM_UNDO'
              AND (item_before_id IS NULL OR item_after_id IS NULL)
              AND extra_json IS NOT NULL
            """
        ).fetchall()
        updates: list[tuple[int | None, int | None, int]] = []
        for row in rows:
            try:
                extra = json.loads(row["extra_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(extra, dict):
                continue
            before = extra.get("beforeId")
            after = extra.get("afterId")
            try:
                before_id = int(before) if before is not None else None
            except (TypeError, ValueError, OverflowError):
                before_id = None
            try:
                after_id = int(after) if after is not None else None
            except (TypeError, ValueError, OverflowError):
                after_id = None
            if before_id is not None and not 0 <= before_id <= 2**63 - 1:
                before_id = None
            if after_id is not None and not 0 <= after_id <= 2**63 - 1:
                after_id = None
            if before_id is not None or after_id is not None:
                updates.append((before_id, after_id, int(row["id"])))
        if updates:
            connection.executemany(
                """
                UPDATE timeline_events
                SET item_before_id = COALESCE(item_before_id, ?),
                    item_after_id = COALESCE(item_after_id, ?)
                WHERE id = ?
                """,
                updates,
            )

    def upsert_player(
        self,
        account: RiotAccount,
        platform_region: str | None = None,
        routing_region: str | None = None,
    ) -> None:
        """Insert or refresh the locally configured player."""

        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO players (
                    puuid, game_name, tag_line, region, platform_region, routing_region
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(puuid) DO UPDATE SET
                    game_name = excluded.game_name,
                    tag_line = excluded.tag_line,
                    region = excluded.region,
                    platform_region = excluded.platform_region,
                    routing_region = excluded.routing_region
                """,
                (
                    account.puuid,
                    account.game_name,
                    account.tag_line,
                    routing_region,
                    platform_region,
                    routing_region,
                ),
            )

    def insert_match(self, match: ParsedMatch) -> bool:
        """Atomically insert a match and participants; return false if already known."""

        with self.connection() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO matches
                    (match_id, patch, queue_id, duration, game_creation)
                VALUES (?, ?, ?, ?, ?)
                """,
                (match.match_id, match.patch, match.queue_id, match.duration, match.game_creation),
            )
            if cursor.rowcount == 0:
                if connection.execute("SELECT 1 FROM participants WHERE match_id = ? LIMIT 1",
                                      (match.match_id,)).fetchone():
                    return False
                # Repair a legacy orphan within the same transaction as participants.
                connection.execute("""UPDATE matches SET patch=?, queue_id=?, duration=?, game_creation=?
                                      WHERE match_id=?""",
                                   (match.patch, match.queue_id, match.duration, match.game_creation, match.match_id))

            connection.executemany(
                """
                INSERT INTO participants (
                    match_id, puuid, teammate_game_name, teammate_tag_line,
                    champion, role, win, kills, deaths, assists,
                    gold_earned, cs_total, damage_dealt, damage_share,
                    vision_score, items, side
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        match.match_id,
                        participant.puuid,
                        participant.teammate_game_name,
                        participant.teammate_tag_line,
                        participant.champion,
                        participant.role,
                        int(participant.win),
                        participant.kills,
                        participant.deaths,
                        participant.assists,
                        participant.gold_earned,
                        participant.cs_total,
                        participant.damage_dealt,
                        participant.damage_share,
                        participant.vision_score,
                        json.dumps(participant.items, separators=(",", ":")),
                        participant.side,
                    )
                    for participant in match.participants
                ],
            )
        return True

    def existing_match_ids(self, match_ids: Sequence[str]) -> set[str]:
        """Return the subset of IDs already present, safely handling SQLite limits."""

        if not match_ids:
            return set()
        existing: set[str] = set()
        with self.connection() as connection:
            for offset in range(0, len(match_ids), 900):
                chunk = match_ids[offset : offset + 900]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""SELECT match_id FROM matches AS m WHERE match_id IN ({placeholders})
                        AND EXISTS (SELECT 1 FROM participants AS p WHERE p.match_id = m.match_id)""",  # noqa: S608
                    tuple(chunk),
                )
                existing.update(row["match_id"] for row in rows)
        return existing

    def match_exists(self, match_id: str) -> bool:
        """Check whether one match is already stored."""

        return match_id in self.existing_match_ids([match_id])

    def get_sync_state(self, puuid: str) -> str | None:
        """Return the newest match anchor recorded for a player."""

        with self.connection() as connection:
            row = connection.execute(
                "SELECT last_match_id_synced FROM sync_state WHERE puuid = ?", (puuid,)
            ).fetchone()
        return row["last_match_id_synced"] if row else None

    def update_sync_state(self, puuid: str, last_match_id: str | None) -> None:
        """Persist the sync anchor and completion time."""

        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO sync_state (puuid, last_match_id_synced, last_sync_at)
                VALUES (?, ?, ?)
                ON CONFLICT(puuid) DO UPDATE SET
                    last_match_id_synced = excluded.last_match_id_synced,
                    last_sync_at = excluded.last_sync_at
                """,
                (puuid, last_match_id, int(time.time())),
            )

    def count_matches(self) -> int:
        """Return the total number of locally stored matches."""

        with self.connection() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM matches").fetchone()[0])

    def count_participants(self) -> int:
        """Return the total number of locally stored participant rows."""

        with self.connection() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM participants").fetchone()[0])

    def find_player(self, game_name: str, tag_line: str) -> dict[str, object] | None:
        """Find a locally known player from the configured Riot ID."""

        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT puuid, game_name, tag_line, region,
                       platform_region, routing_region
                FROM players
                WHERE game_name = ? COLLATE NOCASE AND tag_line = ? COLLATE NOCASE
                LIMIT 1
                """,
                (game_name, tag_line),
            ).fetchone()
        return dict(row) if row else None

    def list_players(self) -> list[dict[str, object]]:
        """List explicitly registered profiles with their own library counts."""

        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT p.puuid, p.game_name, p.tag_line, p.region,
                       p.platform_region, p.routing_region,
                       (SELECT COUNT(*) FROM participants AS own
                        WHERE own.puuid = p.puuid) AS local_games,
                       s.last_sync_at,
                       (SELECT MAX(m.game_creation) FROM participants own
                        JOIN matches m ON m.match_id = own.match_id
                        WHERE own.puuid = p.puuid) AS last_activity_at
                FROM players AS p
                LEFT JOIN sync_state AS s ON s.puuid = p.puuid
                ORDER BY p.game_name COLLATE NOCASE, p.tag_line COLLATE NOCASE, p.puuid
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_player(self, puuid: str) -> dict[str, object] | None:
        """Read one registered profile by stable identity, even after a rename."""

        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT p.puuid, p.game_name, p.tag_line, p.region,
                       p.platform_region, p.routing_region,
                       (SELECT COUNT(*) FROM participants AS own
                        WHERE own.puuid = p.puuid) AS local_games,
                       s.last_sync_at,
                       (SELECT MAX(m.game_creation) FROM participants own
                        JOIN matches m ON m.match_id = own.match_id
                        WHERE own.puuid = p.puuid) AS last_activity_at
                FROM players AS p
                LEFT JOIN sync_state AS s ON s.puuid = p.puuid
                WHERE p.puuid = ?
                """,
                (puuid,),
            ).fetchone()
        return dict(row) if row else None

    def profile_removal_counts(self, puuid: str) -> dict[str, int]:
        """Read-only confirmation preview; deletion recalculates under its lock."""
        with self.connection() as connection:
            return self._profile_removal_counts(connection, puuid)

    @staticmethod
    def _profile_removal_counts(connection, puuid: str) -> dict[str, int]:
        rows = connection.execute("""
            SELECT mine.match_id, EXISTS (
                SELECT 1 FROM participants other JOIN players registered ON registered.puuid = other.puuid
                WHERE other.match_id = mine.match_id AND other.puuid <> ?
            ) AS shared
            FROM participants mine WHERE mine.puuid = ?
        """, (puuid, puuid)).fetchall()
        return {"exclusive": sum(not row["shared"] for row in rows),
                "shared": sum(bool(row["shared"]) for row in rows)}

    def remove_library_profile(self, puuid: str, primary_name: str, primary_tag: str) -> ProfileRemovalResult:
        """Delete a secondary profile and ONLY its exclusive match graph.

        Resolve the protected primary inside the same write transaction. If it
        is absent or ambiguous, fail closed instead of guessing its identity.
        Every other registered profile protects shared matches, regardless of
        which import originally downloaded them. Unregistered participants do
        not constitute ownership. The whole deletion rolls back on any failure.
        """
        from core.import_lock import ImportLock
        from core.backups import backup_before_deletion
        with ImportLock(self.path), self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            primary = connection.execute(
                "SELECT puuid FROM players WHERE game_name = ? COLLATE NOCASE "
                "AND tag_line = ? COLLATE NOCASE", (primary_name, primary_tag),
            ).fetchall()
            if len(primary) != 1:
                raise ValueError("Profil principal non identifiable : retrait désactivé par sécurité.")
            if puuid == primary[0]["puuid"]:
                raise ValueError("Le profil principal ne peut pas être retiré.")
            if not connection.execute("SELECT 1 FROM players WHERE puuid = ?", (puuid,)).fetchone():
                return ProfileRemovalResult(False)
            counts = self._profile_removal_counts(connection, puuid)
            backup = backup_before_deletion(self.path)
            connection.execute("""
                DELETE FROM matches WHERE match_id IN (
                    SELECT mine.match_id FROM participants mine WHERE mine.puuid = ?
                    AND NOT EXISTS (
                        SELECT 1 FROM participants other JOIN players registered ON registered.puuid = other.puuid
                        WHERE other.match_id = mine.match_id AND other.puuid <> ?
                    )
                )
            """, (puuid, puuid))
            # All match-owned participants, timelines, events and identity
            # checkpoints cascade from matches. Sync state cascades from players.
            connection.execute("DELETE FROM players WHERE puuid = ?", (puuid,))
            return ProfileRemovalResult(True, counts["exclusive"], counts["shared"], backup.path, backup.created_at)

    def participant_identity(self, puuid: str) -> dict[str, object] | None:
        """Read the latest usable local Riot identity without registering it."""

        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT p.puuid, p.teammate_game_name AS game_name,
                       p.teammate_tag_line AS tag_line, m.match_id
                FROM participants AS p
                JOIN matches AS m ON m.match_id = p.match_id
                WHERE p.puuid = ?
                  AND LENGTH(TRIM(COALESCE(p.teammate_game_name, ''))) > 0
                  AND LENGTH(TRIM(COALESCE(p.teammate_tag_line, ''))) > 0
                ORDER BY m.game_creation DESC, m.match_id DESC
                LIMIT 1
                """,
                (puuid,),
            ).fetchone()
        return dict(row) if row else None

    def last_sync_at(self, puuid: str) -> int | None:
        """Return the last successful sync timestamp for one player."""

        with self.connection() as connection:
            row = connection.execute(
                "SELECT last_sync_at FROM sync_state WHERE puuid = ?", (puuid,)
            ).fetchone()
        return int(row["last_sync_at"]) if row and row["last_sync_at"] is not None else None

    def count_player_matches(self, puuid: str) -> int:
        """Count stored matches that contain the selected player."""

        with self.connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM participants WHERE puuid = ?", (puuid,)
            ).fetchone()
        return int(row[0])

    def player_matches(self, puuid: str, limit: int | None = None) -> list[dict[str, object]]:
        """Read one participant row per match, newest first by explicit creation time."""

        columns = """
            SELECT m.match_id, m.patch, m.queue_id, m.duration, m.game_creation,
                   p.champion, p.role, p.win, p.kills, p.deaths, p.assists,
                   p.gold_earned, p.cs_total, p.damage_dealt, p.damage_share,
                   p.vision_score, p.items, p.side
            FROM matches AS m
            JOIN participants AS p ON p.match_id = m.match_id
            WHERE p.puuid = ?
            ORDER BY m.game_creation DESC, m.match_id DESC
        """
        parameters: tuple[object, ...] = (puuid,)
        if limit is not None:
            if limit < 1:
                raise ValueError("limit must be positive or None")
            columns += " LIMIT ?"
            parameters = (puuid, limit)

        with self.connection() as connection:
            rows = connection.execute(columns, parameters).fetchall()
        return [dict(row) for row in rows]

    def recent_games_for_player(self, puuid: str, limit: int = 20) -> list[dict[str, object]]:
        """Read recent player performances for CLI output and future analytics."""

        return self.player_matches(puuid, limit)

    def save_timeline(
        self,
        match_id: str,
        frames: Sequence[Mapping[str, Any]],
        events: Sequence[Mapping[str, Any]],
        fetched_at: int | None = None,
        *, dropped_item_events: int = 0,
    ) -> None:
        """Atomically replace one normalized timeline and mark it available."""

        if not frames:
            raise ValueError("Cannot mark an empty timeline available.")
        stored_at = int(time.time()) if fetched_at is None else int(fetched_at)
        with self.connection() as connection:
            connection.execute("DELETE FROM timeline_frames WHERE match_id = ?", (match_id,))
            connection.execute("DELETE FROM timeline_events WHERE match_id = ?", (match_id,))
            connection.executemany(
                """
                INSERT INTO timeline_frames (
                    match_id, participant_id, puuid, timestamp_ms, total_gold,
                    current_gold, xp, level, minions_killed, jungle_minions_killed,
                    cs_total, position_x, position_y
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        match_id,
                        frame.get("participant_id"),
                        frame.get("puuid"),
                        frame.get("timestamp_ms"),
                        frame.get("total_gold"),
                        frame.get("current_gold"),
                        frame.get("xp"),
                        frame.get("level"),
                        frame.get("minions_killed"),
                        frame.get("jungle_minions_killed"),
                        frame.get("cs_total"),
                        frame.get("position_x"),
                        frame.get("position_y"),
                    )
                    for frame in frames
                ],
            )
            connection.executemany(
                """
                INSERT INTO timeline_events (
                    match_id, event_index, timestamp_ms, event_type, participant_id,
                    killer_id, victim_id, creator_id, item_id, item_before_id,
                    item_after_id, team_id, monster_type,
                    monster_subtype, building_type, tower_type, lane_type, extra_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        match_id,
                        event.get("event_index"),
                        event.get("timestamp_ms"),
                        event.get("event_type"),
                        event.get("participant_id"),
                        event.get("killer_id"),
                        event.get("victim_id"),
                        event.get("creator_id"),
                        event.get("item_id"),
                        event.get("item_before_id"),
                        event.get("item_after_id"),
                        event.get("team_id"),
                        event.get("monster_type"),
                        event.get("monster_subtype"),
                        event.get("building_type"),
                        event.get("tower_type"),
                        event.get("lane_type"),
                        json.dumps(event.get("extra", {}), separators=(",", ":")),
                    )
                    for event in events
                ],
            )
            connection.execute(
                """
                INSERT INTO timeline_status (
                    match_id, status, fetched_at, frame_count, event_count, last_error, dropped_item_events
                ) VALUES (?, 'available', ?, ?, ?, NULL, ?)
                ON CONFLICT(match_id) DO UPDATE SET
                    status = 'available', fetched_at = excluded.fetched_at,
                    frame_count = excluded.frame_count, event_count = excluded.event_count,
                    last_error = NULL, dropped_item_events = excluded.dropped_item_events
                """,
                (match_id, stored_at, len(frames), len(events), dropped_item_events),
            )

    def set_timeline_status(self, match_id: str, status: str, error: str | None = None) -> None:
        """Record an unavailable or failed fetch without deleting valid data."""

        if status not in {"missing", "unavailable", "error"}:
            raise ValueError(f"Unsupported timeline status: {status}")
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO timeline_status (
                    match_id, status, fetched_at, frame_count, event_count, last_error
                ) VALUES (?, ?, ?, 0, 0, ?)
                ON CONFLICT(match_id) DO UPDATE SET
                    status = excluded.status, fetched_at = excluded.fetched_at,
                    frame_count = 0, event_count = 0, last_error = excluded.last_error
                WHERE timeline_status.status != 'available'
                   OR NOT EXISTS (SELECT 1 FROM timeline_frames AS tf
                                  WHERE tf.match_id = timeline_status.match_id)
                """,
                (match_id, status, int(time.time()), error),
            )

    def timeline_candidates(
        self, puuid: str, limit: int | None = None, retry_errors: bool = False
    ) -> list[str]:
        """Return newest local matches whose timeline should be requested."""

        statuses = ["missing"]
        if retry_errors:
            statuses.append("error")
        placeholders = ",".join("?" for _ in statuses)
        query = f"""
            SELECT m.match_id
            FROM matches AS m
            JOIN participants AS p ON p.match_id = m.match_id AND p.puuid = ?
            LEFT JOIN timeline_status AS ts ON ts.match_id = m.match_id
            WHERE ts.match_id IS NULL OR ts.status IN ({placeholders})
               OR (ts.status = 'available' AND (
                   SELECT COUNT(DISTINCT tf.participant_id) FROM timeline_frames AS tf
                   WHERE tf.match_id = m.match_id AND tf.puuid = p.puuid
                     AND tf.timestamp_ms >= 0) != 1)
            ORDER BY m.game_creation DESC, m.match_id DESC
        """
        parameters: list[object] = [puuid, *statuses]
        if limit is not None:
            if limit < 1:
                raise ValueError("limit must be positive or None")
            query += " LIMIT ?"
            parameters.append(limit)
        with self.connection() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        return [str(row["match_id"]) for row in rows]

    def timeline_coverage(self, puuid: str) -> dict[str, int]:
        """Return local coverage and normalized row totals in one bounded query."""

        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT CASE WHEN ts.status = 'available' AND (
                    SELECT COUNT(DISTINCT tf.participant_id) FROM timeline_frames AS tf
                    WHERE tf.match_id = m.match_id AND tf.puuid = p.puuid
                      AND tf.timestamp_ms >= 0) != 1
                    THEN 'missing' ELSE COALESCE(ts.status, 'missing') END AS status,
                    COUNT(*) AS count
                FROM matches AS m
                JOIN participants AS p ON p.match_id = m.match_id AND p.puuid = ?
                LEFT JOIN timeline_status AS ts ON ts.match_id = m.match_id
                GROUP BY 1
                """,
                (puuid,),
            ).fetchall()
            frame_count = int(
                connection.execute("""SELECT COUNT(*) FROM timeline_frames WHERE match_id IN
                    (SELECT match_id FROM participants WHERE puuid = ?)""", (puuid,)).fetchone()[0]
            )
            event_count = int(
                connection.execute("""SELECT COUNT(*) FROM timeline_events WHERE match_id IN
                    (SELECT match_id FROM participants WHERE puuid = ?)""", (puuid,)).fetchone()[0]
            )
        counts = {"available": 0, "unavailable": 0, "error": 0, "missing": 0}
        counts.update({str(row["status"]): int(row["count"]) for row in rows})
        counts["local"] = sum(counts[key] for key in ("available", "unavailable", "error", "missing"))
        counts["frames"] = frame_count
        counts["events"] = event_count
        return counts

    def timeline_available_match_ids(self, puuid: str | None = None) -> set[str]:
        """Return available timeline IDs, optionally restricted to one player."""

        if puuid is None:
            query = """SELECT ts.match_id FROM timeline_status AS ts WHERE status = 'available'
                AND EXISTS (SELECT 1 FROM timeline_frames AS tf WHERE tf.match_id = ts.match_id
                            AND tf.timestamp_ms >= 0)"""
            parameters: tuple[object, ...] = ()
        else:
            query = """
                SELECT ts.match_id FROM timeline_status AS ts
                JOIN participants AS p ON p.match_id = ts.match_id
                WHERE ts.status = 'available' AND p.puuid = ?
                  AND (SELECT COUNT(DISTINCT tf.participant_id) FROM timeline_frames AS tf
                              WHERE tf.match_id = ts.match_id AND tf.puuid = p.puuid
                                AND tf.timestamp_ms >= 0) = 1
            """
            parameters = (puuid,)
        with self.connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return {str(row["match_id"]) for row in rows}

    def timeline_frames(self, match_id: str) -> list[dict[str, object]]:
        """Load every participant frame for one match in deterministic order."""

        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM timeline_frames WHERE match_id = ?
                ORDER BY timestamp_ms, participant_id
                """,
                (match_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def timeline_events(self, match_id: str) -> list[dict[str, object]]:
        """Load normalized events for one match in source order."""

        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM timeline_events WHERE match_id = ?
                ORDER BY timestamp_ms, event_index
                """,
                (match_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def match_participants(self, match_id: str) -> list[dict[str, object]]:
        """Load participants required for side and direct-opponent resolution."""

        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT puuid, teammate_game_name, teammate_tag_line,
                       champion, role, side, win, kills, deaths, assists,
                       gold_earned, cs_total, damage_dealt, vision_score
                FROM participants WHERE match_id = ? ORDER BY id
                """,
                (match_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def history_item_events(self, puuid: str, match_id: str) -> list[dict[str, object]]:
        """Two indexed reads for the one expanded card; no roster-wide N+1.

        The frame mapping must be unique, and the player must belong to the
        match. Only explicit event fields leave this local read boundary.
        """
        with self.connection() as connection:
            identifiers = connection.execute(
                "SELECT DISTINCT f.participant_id FROM timeline_frames f "
                "WHERE f.match_id=? AND f.puuid=? AND EXISTS "
                "(SELECT 1 FROM participants p WHERE p.match_id=f.match_id AND p.puuid=?)",
                (match_id, puuid, puuid),
            ).fetchall()
            if len(identifiers) != 1:
                return []
            rows = connection.execute(
                "SELECT e.timestamp_ms,e.event_type,e.item_id,e.item_before_id,e.item_after_id "
                "FROM timeline_events e JOIN matches m ON m.match_id=e.match_id "
                "WHERE e.match_id=? AND e.participant_id=? "
                "AND e.event_type IN ('ITEM_PURCHASED','ITEM_SOLD','ITEM_UNDO') "
                "AND e.timestamp_ms BETWEEN 0 AND 86400000 "
                "AND (m.duration IS NULL OR e.timestamp_ms <= m.duration*1000) "
                "ORDER BY e.timestamp_ms,e.event_index", (match_id, identifiers[0][0]),
            ).fetchall()
        return [dict(row) for row in rows]

    def participants_for_player_matches(self, puuid: str) -> list[dict[str, object]]:
        """Bulk-load all participants from the selected player's local matches."""

        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT p.match_id, p.puuid, p.teammate_game_name, p.teammate_tag_line,
                       p.champion, p.role, p.side, p.win, p.kills, p.deaths,
                       p.assists, p.gold_earned, p.cs_total, p.damage_dealt,
                       p.vision_score, p.items, m.game_creation, m.duration, m.patch, m.queue_id
                FROM participants AS p
                JOIN matches AS m ON m.match_id = p.match_id
                WHERE p.match_id IN (
                    SELECT match_id FROM participants WHERE puuid = ?
                )
                ORDER BY m.game_creation, m.match_id, p.id
                """,
                (puuid,),
            ).fetchall()
        return [dict(row) for row in rows]

    def timeline_analysis_source(
        self, puuid: str
    ) -> tuple[
        list[dict[str, object]],
        list[dict[str, object]],
        list[dict[str, object]],
        list[dict[str, object]],
    ]:
        """Bulk-load all available timeline inputs without an N+1 query pattern."""

        with self.connection() as connection:
            matches = connection.execute(
                """
                SELECT m.match_id, m.duration, m.game_creation, m.patch, m.queue_id,
                       p.champion, p.role, p.side, p.win, ts.dropped_item_events
                FROM matches AS m
                JOIN participants AS p ON p.match_id = m.match_id AND p.puuid = ?
                JOIN timeline_status AS ts ON ts.match_id = m.match_id
                WHERE ts.status = 'available'
                  AND (SELECT COUNT(DISTINCT tf.participant_id) FROM timeline_frames AS tf
                              WHERE tf.match_id = m.match_id AND tf.puuid = p.puuid
                                AND tf.timestamp_ms >= 0) = 1
                ORDER BY m.game_creation DESC, m.match_id DESC
                """,
                (puuid,),
            ).fetchall()
            participants = connection.execute(
                """
                SELECT p.match_id, p.puuid, p.teammate_game_name, p.teammate_tag_line,
                       p.champion, p.role, p.side, p.win, p.kills, p.deaths,
                       p.assists, p.gold_earned, p.cs_total, p.damage_dealt,
                       p.vision_score
                FROM participants AS p
                WHERE p.match_id IN (
                    SELECT ts.match_id FROM timeline_status AS ts
                    JOIN participants AS own ON own.match_id = ts.match_id
                    WHERE ts.status = 'available' AND own.puuid = ?
                      AND (SELECT COUNT(DISTINCT tf.participant_id) FROM timeline_frames AS tf
                           WHERE tf.match_id = ts.match_id AND tf.puuid = own.puuid
                             AND tf.timestamp_ms >= 0) = 1
                )
                ORDER BY p.match_id, p.id
                """,
                (puuid,),
            ).fetchall()
            frames = connection.execute(
                """
                SELECT tf.* FROM timeline_frames AS tf
                WHERE tf.match_id IN (
                    SELECT ts.match_id FROM timeline_status AS ts
                    JOIN participants AS own ON own.match_id = ts.match_id
                    WHERE ts.status = 'available' AND own.puuid = ?
                      AND (SELECT COUNT(DISTINCT tf.participant_id) FROM timeline_frames AS tf
                           WHERE tf.match_id = ts.match_id AND tf.puuid = own.puuid
                             AND tf.timestamp_ms >= 0) = 1
                )
                ORDER BY tf.match_id, tf.timestamp_ms, tf.participant_id
                """,
                (puuid,),
            ).fetchall()
            events = connection.execute(
                """
                SELECT te.* FROM timeline_events AS te
                WHERE te.match_id IN (
                    SELECT ts.match_id FROM timeline_status AS ts
                    JOIN participants AS own ON own.match_id = ts.match_id
                    WHERE ts.status = 'available' AND own.puuid = ?
                      AND (SELECT COUNT(DISTINCT tf.participant_id) FROM timeline_frames AS tf
                           WHERE tf.match_id = ts.match_id AND tf.puuid = own.puuid
                             AND tf.timestamp_ms >= 0) = 1
                )
                ORDER BY te.match_id, te.timestamp_ms, te.event_index
                """,
                (puuid,),
            ).fetchall()
        return (
            [dict(row) for row in matches],
            [dict(row) for row in participants],
            [dict(row) for row in frames],
            [dict(row) for row in events],
        )
