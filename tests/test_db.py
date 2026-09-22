from __future__ import annotations

import sqlite3
from pathlib import Path

from core.db import Database
from core.models import RiotAccount, parse_riot_match


def test_database_creates_required_tables(database: Database) -> None:
    with database.connection() as connection:
        names = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {
        "players", "matches", "participants", "sync_state",
        "timeline_status", "timeline_frames", "timeline_events",
    } <= names
    with database.connection() as connection:
        indexes = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }
    assert {
        "idx_matches_creation",
        "idx_participants_match_id",
        "idx_participants_puuid",
        "idx_participants_champion",
        "idx_timeline_frames_match",
        "idx_timeline_frames_puuid_time",
        "idx_timeline_events_match_time",
        "idx_timeline_events_type",
    } <= indexes


def test_timeline_migration_preserves_existing_match_counts(
    database: Database, riot_match_payload: dict[str, object]
) -> None:
    database.insert_match(parse_riot_match(riot_match_payload))
    before = (database.count_matches(), database.count_participants())
    database.initialize()
    database.initialize()
    assert (database.count_matches(), database.count_participants()) == before
    with database.connection() as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_player_match_and_participants_are_inserted(
    database: Database, riot_match_payload: dict[str, object]
) -> None:
    account = RiotAccount(puuid="player-puuid", gameName="Synthetic Player", tagLine="EUW")
    database.upsert_player(account, "europe")
    inserted = database.insert_match(parse_riot_match(riot_match_payload))

    assert inserted is True
    assert database.count_matches() == 1
    assert database.count_participants() == 4
    with database.connection() as connection:
        player = connection.execute(
            "SELECT game_name, tag_line FROM players WHERE puuid = ?", ("player-puuid",)
        ).fetchone()
    assert dict(player) == {"game_name": "Synthetic Player", "tag_line": "EUW"}


def test_reinserting_match_is_idempotent(
    database: Database, riot_match_payload: dict[str, object]
) -> None:
    match = parse_riot_match(riot_match_payload)
    assert database.insert_match(match) is True
    assert database.insert_match(match) is False
    assert database.count_matches() == 1
    assert database.count_participants() == 4


def test_region_migration_is_additive_idempotent_and_preserves_player(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE players (puuid TEXT PRIMARY KEY, game_name TEXT NOT NULL, tag_line TEXT NOT NULL, region TEXT)"
        )
        connection.execute(
            "INSERT INTO players VALUES (?, ?, ?, ?)",
            ("player-puuid", "Synthetic Player", "EUW", "europe"),
        )

    database = Database(path)
    database.initialize()
    database.initialize()
    database.upsert_player(
        RiotAccount(puuid="player-puuid", gameName="Synthetic Player", tagLine="EUW"),
        "EUW1",
        "EUROPE",
    )
    player = database.find_player("Synthetic Player", "EUW")

    assert player is not None
    assert player["puuid"] == "player-puuid"
    assert player["tag_line"] == "EUW"
    assert player["platform_region"] == "EUW1"
    assert player["routing_region"] == "EUROPE"
