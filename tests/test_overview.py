from __future__ import annotations

from copy import deepcopy

import polars as pl
import pytest

from analytics.overview import (
    PLAYER_MATCH_SCHEMA,
    champion_summary,
    get_overview_summary,
    get_player_matches,
    recent_matches,
    rolling_winrate,
)
from core.db import Database
from core.models import parse_riot_match


def _overview_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "match_id": ["M3", "M2", "M1"],
            "game_creation": [3_000, 2_000, 1_000],
            "champion": ["Shyvana", "Lulu", "Shyvana"],
            "win": [1, 0, 1],
            "kills": [8, 2, 4],
            "deaths": [2, 4, 0],
            "assists": [10, 8, 6],
            "duration": [1_800, 240, 1_200],
            "cs_total": [210, 20, 140],
            "damage_dealt": [18_000, None, 12_000],
        }
    )


def test_get_player_matches_selects_latest_n_in_explicit_order(
    database: Database, riot_match_payload: dict[str, object]
) -> None:
    for index, creation in enumerate((1_000, 3_000, 2_000), start=1):
        payload = deepcopy(riot_match_payload)
        payload["metadata"]["matchId"] = f"MATCH_{index}"  # type: ignore[index]
        payload["info"]["gameCreation"] = creation  # type: ignore[index]
        database.insert_match(parse_riot_match(payload))

    matches = get_player_matches(database, "player-puuid", limit=2)

    assert matches["match_id"].to_list() == ["MATCH_2", "MATCH_3"]
    assert matches["game_creation"].to_list() == [3_000, 2_000]


def test_overview_summary_includes_short_games() -> None:
    summary = get_overview_summary(_overview_frame())

    assert summary["games"] == 3
    assert summary["wins"] == 2
    assert summary["losses"] == 1
    assert summary["winrate"] == pytest.approx(66.6667, rel=1e-4)
    assert summary["kda"] == pytest.approx((9 + 2.5 + 10) / 3)
    assert summary["short_games"] == 1


def test_champion_summary_groups_and_sorts_by_games() -> None:
    champions = champion_summary(_overview_frame())

    assert champions["champion"].to_list() == ["Shyvana", "Lulu"]
    shyvana = champions.row(0, named=True)
    assert shyvana["games"] == 2
    assert shyvana["wins"] == 2
    assert shyvana["losses"] == 0
    assert shyvana["winrate"] == 100.0
    assert shyvana["kda"] == pytest.approx(9.5)


def test_rolling_winrate_is_chronological_and_uses_five_game_window() -> None:
    frame = pl.concat([_overview_frame(), _overview_frame().with_columns(
        (pl.col("match_id") + "x").alias("match_id"),
        (pl.col("game_creation") + 3_000).alias("game_creation"),
    )])
    rolling, window = rolling_winrate(frame)

    assert window == 5
    assert rolling["match_number"].to_list() == [5, 6]
    assert rolling["rolling_winrate"].to_list() == pytest.approx([60.0, 60.0])
    assert rolling["rolling_wins"].to_list() == [3, 3]


def test_empty_and_single_game_datasets_are_supported() -> None:
    empty = pl.DataFrame(schema=PLAYER_MATCH_SCHEMA)
    empty_summary = get_overview_summary(empty)
    empty_rolling, window = rolling_winrate(empty)

    assert empty_summary["games"] == 0
    assert empty_summary["winrate"] == 0.0
    assert champion_summary(empty).is_empty()
    assert empty_rolling.is_empty()
    assert window == 5

    single = _overview_frame().head(1)
    single_summary = get_overview_summary(single)
    single_rolling, single_window = rolling_winrate(single)
    assert single_summary["games"] == 1
    assert single_summary["winrate"] == 100.0
    assert single_rolling.is_empty()
    assert single_window == 5


def test_missing_metrics_are_not_invented() -> None:
    games = pl.DataFrame(
        {
            "win": [1],
            "kills": [None],
            "deaths": [None],
            "assists": [None],
            "duration": [60],
            "cs_total": [None],
            "damage_dealt": [None],
        }
    )
    summary = get_overview_summary(games)

    assert summary["kda"] is None
    assert summary["cs_per_minute"] is None
    assert summary["damage_per_minute"] is None
    assert summary["short_games"] == 1


def test_recent_matches_restores_newest_first_order() -> None:
    recent = recent_matches(_overview_frame().reverse(), count=2)
    assert recent["match_id"].to_list() == ["M3", "M2"]
