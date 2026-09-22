from __future__ import annotations

import json

import polars as pl

from analytics.game_flow import death_profile, objective_context, trajectory_preset
from analytics.timeline import analyze_match_timeline


def _games() -> pl.DataFrame:
    return pl.DataFrame({
        "match_id": ["a", "b", "c", "d", "short", "missing"],
        "duration": [1800] * 6,
        "win": [1, 0, 1, 0, 1, 0],
        "kills": [1] * 6, "deaths": [1] * 6, "assists": [1] * 6,
        "cs_total": [100] * 6, "gold_earned": [10000] * 6,
        "damage_dealt": [10000] * 6, "vision_score": [20] * 6,
        "trajectory": [
            "AHEAD → AHEAD → AHEAD", "AHEAD → EVEN → BEHIND",
            "BEHIND → EVEN → AHEAD", "EVEN → EVEN → EVEN", None, None,
        ],
        "first_dragon": ["ours", "theirs", "none", "ours", "none", None],
        "first_herald": ["none"] * 6,
        "first_tower": ["ours"] * 6,
        "death_profile": ["0 deaths before 10", "1 death before 10", "2+ deaths before 10", "0 deaths before 10", "1 death before 10", None],
        "gold_diff_15": [100, -100, 50, 0, None, None],
        "team_gold_diff_15": [1200, 0, -1200, 0, None, None],
    })


def test_trajectory_short_and_missing_remain_na() -> None:
    rows = trajectory_preset(_games())
    assert len(rows) == 4
    assert sum(row["games"] for row in rows) == 4


def test_objective_and_death_presets_keep_explicit_groups() -> None:
    objectives = objective_context(_games())
    assert {row["label"] for row in objectives["first_dragon"]} == {"ours", "theirs", "none"}
    assert [row["label"] for row in death_profile(_games())] == [
        "0 deaths before 10", "1 death before 10", "2+ deaths before 10"
    ]


def test_equal_first_objective_timestamps_are_ambiguous() -> None:
    frames = []
    for timestamp in (600_000, 900_000, 1_200_000):
        for participant_id, puuid, gold in ((1, "player", 5000), (2, "enemy", 4500)):
            frames.append({"participant_id": participant_id, "puuid": puuid, "timestamp_ms": timestamp, "total_gold": gold, "cs_total": 50, "xp": 3000, "level": 7})
    events = [
        {"timestamp_ms": 700_000, "event_type": "ELITE_MONSTER_KILL", "team_id": 100, "monster_type": "DRAGON", "extra_json": json.dumps({})},
        {"timestamp_ms": 700_000, "event_type": "ELITE_MONSTER_KILL", "team_id": 200, "monster_type": "DRAGON", "extra_json": json.dumps({})},
    ]
    result = analyze_match_timeline(
        {"match_id": "x", "duration": 1800, "win": 1},
        [
            {"puuid": "player", "role": "JUNGLE", "side": "blue", "champion": "Shyvana"},
            {"puuid": "enemy", "role": "JUNGLE", "side": "red", "champion": "Nocturne"},
        ], frames, events, "player"
    )
    assert result["first_dragon"] == "ambiguous"


def test_objective_owner_uses_killer_team_and_tower_team_is_destroyed_side() -> None:
    frames = []
    for timestamp in (600_000, 900_000, 1_200_000):
        for participant_id, puuid in ((1, "player"), (2, "enemy")):
            frames.append({"participant_id": participant_id, "puuid": puuid, "timestamp_ms": timestamp, "total_gold": 5000, "cs_total": 50, "xp": 3000, "level": 7})
    events = [
        {"timestamp_ms": 700_000, "event_type": "ELITE_MONSTER_KILL", "killer_id": 1, "team_id": None, "monster_type": "DRAGON", "extra_json": "{}"},
        {"timestamp_ms": 800_000, "event_type": "BUILDING_KILL", "killer_id": 1, "team_id": 200, "building_type": "TOWER_BUILDING", "extra_json": "{}"},
    ]
    result = analyze_match_timeline(
        {"match_id": "x", "duration": 1800, "win": 1},
        [
            {"puuid": "player", "role": "JUNGLE", "side": "blue", "champion": "Shyvana"},
            {"puuid": "enemy", "role": "JUNGLE", "side": "red", "champion": "Nocturne"},
        ], frames, events, "player"
    )
    assert result["first_dragon"] == "ours"
    assert result["first_tower"] == "ours"
