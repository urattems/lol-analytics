"""Champion-specific analytics kept independent from Streamlit."""

from __future__ import annotations

import polars as pl

from analytics.overview import (
    champion_summary,
    get_overview_summary,
    per_match_kda_expression,
    per_minute_expression,
)


TREND_METRICS = {
    "KDA": per_match_kda_expression,
    "CS/min": lambda: per_minute_expression("cs_total"),
    "Gold/min": lambda: per_minute_expression("gold_earned"),
    "Damage/min": lambda: per_minute_expression("damage_dealt"),
    "Vision/min": lambda: per_minute_expression("vision_score"),
}


def champion_matches(games: pl.DataFrame, champion: str) -> pl.DataFrame:
    """Select one champion and retain newest-first match order."""

    if games.is_empty():
        return games
    return games.filter(pl.col("champion") == champion).sort(
        ["game_creation", "match_id"], descending=[True, True]
    )


def champion_detail_summary(games: pl.DataFrame, champion: str) -> dict[str, object]:
    """Return the detail KPI set for one observed champion."""

    selected = champion_matches(games, champion)
    summary: dict[str, object] = dict(get_overview_summary(selected))
    row = champion_summary(selected)
    if row.is_empty():
        summary.update(
            gold_per_minute=None,
            vision_per_minute=None,
            primary_role=None,
            average_kills=None,
            average_deaths=None,
            average_assists=None,
        )
    else:
        values = row.row(0, named=True)
        summary.update(
            gold_per_minute=values["gold_per_minute"],
            vision_per_minute=values["vision_per_minute"],
            primary_role=values["primary_role"],
            average_kills=values["average_kills"],
            average_deaths=values["average_deaths"],
            average_assists=values["average_assists"],
        )
    return summary


def champion_trend(games: pl.DataFrame, champion: str, metric: str) -> pl.DataFrame:
    """Calculate one selected per-match metric in chronological order."""

    if metric not in TREND_METRICS:
        raise ValueError(f"Unsupported champion trend metric: {metric}")
    selected = champion_matches(games, champion)
    if selected.is_empty():
        return selected.with_columns(pl.lit(None, dtype=pl.Float64).alias("metric_value"))
    return (
        selected.sort(["game_creation", "match_id"])
        .with_row_index("match_number", offset=1)
        .with_columns(TREND_METRICS[metric]().alias("metric_value"))
    )


def games_by_champion(games: pl.DataFrame) -> pl.DataFrame:
    """Backward-compatible public name for the complete champion summary."""

    return champion_summary(games)
