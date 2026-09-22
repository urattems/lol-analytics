from __future__ import annotations

import polars as pl

from analytics.sessions import assign_sessions, session_analytics, session_overview


def _games() -> pl.DataFrame:
    starts = [0, 30 * 60_000, 85 * 60_000, 141 * 60_000]
    return pl.DataFrame({
        "match_id": ["1", "2", "3", "4"],
        "game_creation": starts,
        "duration": [600, 600, 600, 600],
        "champion": ["Shyvana", "Shyvana", "Ahri", "Ahri"],
        "win": [1, 0, 1, 0],
        "kills": [5, 4, 3, 2],
        "deaths": [2, 3, 2, 4],
        "assists": [8, 7, 6, 5],
        "cs_total": [80, 75, 70, 60],
        "damage_dealt": [5000, 4000, 3000, 2000],
        "gold_earned": [6000, 5500, 5000, 4500],
        "vision_score": [10, 11, 12, 13],
    })


def test_session_gap_uses_previous_game_end_and_includes_exact_threshold() -> None:
    annotated = assign_sessions(_games(), 45).sort("match_id")
    assert annotated["session_id"].to_list() == [
        "session_001", "session_001", "session_001", "session_002"
    ]
    assert annotated["minutes_since_previous_game"].to_list() == [None, 20.0, 45.0, 46.0]
    assert annotated["session_game_number"].to_list() == [1, 2, 3, 1]


def test_session_presets_cover_previous_result_and_champion_switch() -> None:
    annotated = assign_sessions(_games(), 45)
    assert session_overview(annotated) == {"sessions": 2, "average_games": 2.0, "longest": 3}
    bundle = session_analytics(annotated)
    assert {row["label"] for row in bundle["previous_result"]} == {"first", "win", "loss"}
    switches = {row["label"]: row["games"] for row in bundle["champion_switch"]}
    assert switches == {False: 1, True: 1}


def test_game_four_and_later_bucket_is_explicit() -> None:
    games = pl.concat([
        _games().head(1).with_columns(
            pl.lit(str(index)).alias("match_id"),
            pl.lit(index * 11 * 60_000).alias("game_creation"),
        )
        for index in range(1, 6)
    ])
    annotated = assign_sessions(games, 45).sort("session_game_number")
    assert annotated["session_game_bucket"].to_list() == ["1", "2", "3", "4+", "4+"]
