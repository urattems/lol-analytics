from __future__ import annotations

import polars as pl

from analytics.matchups import identify_matchups, matchup_detail, matchup_matrix, observed_extremes


def _games(count: int = 12) -> pl.DataFrame:
    first_patch = min(6, count)
    return pl.DataFrame({
        "match_id": [f"m{i}" for i in range(count)],
        "game_creation": list(range(count)),
        "champion": ["Shyvana"] * count,
        "role": ["JUNGLE"] * count,
        "patch": ["16.16"] * first_patch + ["16.17"] * (count - first_patch),
        "queue_id": [420] * count,
        "side": ["blue"] * count,
        "duration": [1800] * count,
        "win": ([1, 0] * ((count + 1) // 2))[:count],
        "kills": [6] * count,
        "deaths": [3] * count,
        "assists": [9] * count,
        "cs_total": [180] * count,
        "gold_earned": [12000] * count,
        "damage_dealt": [15000] * count,
        "vision_score": [20] * count,
        "timeline_available": [True] * count,
        "gold_diff_10": [100] * count,
        "gold_diff_15": [200] * count,
        "cs_diff_10": [2] * count,
        "cs_diff_15": [4] * count,
        "xp_diff_15": [120] * count,
    })


def _participants(count: int = 12) -> list[dict[str, object]]:
    rows = []
    for index in range(count):
        rows.extend([
            {"match_id": f"m{index}", "puuid": "player", "champion": "Shyvana", "role": "JUNGLE", "side": "blue"},
            {"match_id": f"m{index}", "puuid": f"enemy{index}", "champion": "Nocturne", "role": "JUNGLE", "side": "red"},
        ])
    return rows


def test_same_role_opponent_matrix_patch_and_timeline_metrics() -> None:
    identified = identify_matchups(_games(), _participants(), "player")
    assert identified.filter(pl.col("matchup_available")).height == 12
    matrix = matchup_matrix(identified, champion="Shyvana", role="JUNGLE", patch="16.17")
    assert matrix[0]["games"] == 6
    assert matrix[0]["winrate"] == 50.0
    assert matrix[0]["gold_diff_15"] == 200.0
    assert matrix[0]["cs_diff_15"] == 4.0
    assert matrix[0]["xp_diff_15"] == 120.0


def test_ambiguous_and_missing_opponents_are_never_guessed() -> None:
    participants = _participants(2)
    participants.append({"match_id": "m0", "puuid": "enemy-extra", "champion": "Diana", "role": "JUNGLE", "side": "red"})
    participants[:] = [row for row in participants if not (row["match_id"] == "m1" and str(row["puuid"]).startswith("enemy"))]
    identified = identify_matchups(_games(2), participants, "player").sort("match_id")
    assert identified["matchup_available"].to_list() == [False, False]
    assert identified["opponent_champion"].to_list() == [None, None]


def test_observed_extremes_reject_tiny_samples_and_detail_splits_patch() -> None:
    identified = identify_matchups(_games(), _participants(), "player")
    matrix = matchup_matrix(identified)
    favorable, difficult = observed_extremes([*matrix, {**matrix[0], "opponent_champion": "Diana", "games": 1, "wins": 1, "winrate": 100.0}])
    assert all(row["games"] >= 5 for row in [*favorable, *difficult])
    detail = matchup_detail(identified, "Shyvana", "Nocturne")
    assert detail["games"] == 12
    assert [row["games"] for row in detail["patches"]] == [6, 6]
