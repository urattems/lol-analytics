from __future__ import annotations

import polars as pl
import pytest

from analytics.overview import (
    cs_per_minute,
    damage_per_minute,
    game_count,
    kda,
    losses,
    winrate,
    wins,
)


def test_overview_counts_and_rates() -> None:
    games = pl.DataFrame(
        {
            "win": [True, False],
            "kills": [8, 4],
            "deaths": [2, 4],
            "assists": [10, 6],
            "duration": [1800, 1200],
            "cs_total": [210, 140],
            "damage_dealt": [18_000, 12_000],
        }
    )
    assert game_count(games) == 2
    assert wins(games) == 1
    assert losses(games) == 1
    assert winrate(games) == 50.0
    assert kda(games) == pytest.approx((9.0 + 2.5) / 2)
    assert cs_per_minute(games) == 7.0
    assert damage_per_minute(games) == 600.0


def test_kda_uses_one_when_deaths_are_zero() -> None:
    games = [{"kills": 5, "assists": 7, "deaths": 0}]
    assert kda(games) == 12.0


def test_empty_metrics_are_zero() -> None:
    assert game_count([]) == 0
    assert winrate([]) == 0.0


def test_per_match_averages_ignore_unavailable_values() -> None:
    games = pl.DataFrame(
        {
            "kills": [3, None],
            "deaths": [0, 2],
            "assists": [4, None],
            "duration": [600, 0],
            "cs_total": [80, None],
            "damage_dealt": [5_000, None],
        }
    )
    assert kda(games) == 7.0
    assert cs_per_minute(games) == 8.0
    assert damage_per_minute(games) == 500.0
