from __future__ import annotations

import polars as pl
import pytest

from analytics.champions import champion_detail_summary, champion_matches, champion_trend
from analytics.overview import champion_summary


def _games() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "match_id": ["3", "2", "1"],
            "game_creation": [3, 2, 1],
            "champion": ["Shyvana", "Shyvana", "Lulu"],
            "role": ["JUNGLE", "TOP", "UTILITY"],
            "win": [1, 0, 1],
            "kills": [8, 2, 1],
            "deaths": [2, 4, 0],
            "assists": [10, 4, 12],
            "duration": [1_800, 1_200, 600],
            "cs_total": [210, 120, None],
            "gold_earned": [12_000, 8_000, 4_000],
            "damage_dealt": [18_000, 8_000, None],
            "vision_score": [30, 10, 20],
        }
    )


def test_champion_summary_contains_complete_metrics_and_primary_role() -> None:
    result = champion_summary(_games())
    shyvana = result.filter(pl.col("champion") == "Shyvana").row(0, named=True)

    assert shyvana["games"] == 2
    assert shyvana["wins"] == 1
    assert shyvana["losses"] == 1
    assert shyvana["winrate"] == 50.0
    assert shyvana["kda"] == pytest.approx((9 + 1.5) / 2)
    assert shyvana["gold_per_minute"] == 400.0
    assert shyvana["vision_per_minute"] == pytest.approx(0.75)
    assert shyvana["primary_role"] == "JUNGLE"


def test_single_game_and_missing_values_are_supported() -> None:
    detail = champion_detail_summary(_games(), "Lulu")
    assert detail["games"] == 1
    assert detail["winrate"] == 100.0
    assert detail["kda"] == 13.0
    assert detail["cs_per_minute"] is None
    assert detail["damage_per_minute"] is None


def test_empty_champion_dataset_has_clean_results() -> None:
    empty = _games().head(0)
    assert champion_summary(empty).is_empty()
    assert champion_matches(empty, "Shyvana").is_empty()
    detail = champion_detail_summary(empty, "Shyvana")
    assert detail["games"] == 0
    assert detail["gold_per_minute"] is None


def test_champion_trend_is_chronological_and_metric_selectable() -> None:
    trend = champion_trend(_games(), "Shyvana", "Gold/min")
    assert trend["match_id"].to_list() == ["2", "3"]
    assert trend["metric_value"].to_list() == [400.0, 400.0]
    with pytest.raises(ValueError):
        champion_trend(_games(), "Shyvana", "Unsupported")
