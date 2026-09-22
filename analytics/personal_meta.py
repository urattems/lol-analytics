"""Personal champion/role evolution using local time and patch buckets."""

from __future__ import annotations

from datetime import datetime

import polars as pl

from analytics.explorer import analysis_summary


def _period_label(timestamp_ms: int, period: str) -> str:
    value = datetime.fromtimestamp(timestamp_ms / 1000).astimezone()
    if period == "week":
        year, week, _ = value.isocalendar()
        return f"{year}-W{week:02d}"
    return value.strftime("%Y-%m")


def add_time_bucket(games: pl.DataFrame, period: str) -> pl.DataFrame:
    if period == "patch":
        return games.with_columns(pl.col("patch").alias("period"))
    if period not in {"week", "month"}:
        raise ValueError("period must be week, month, or patch")
    return games.with_columns(
        pl.col("game_creation").map_elements(
            lambda value: _period_label(int(value), period), return_dtype=pl.String
        ).alias("period")
    )


def champion_frequency(games: pl.DataFrame, period: str = "patch") -> list[dict[str, object]]:
    if games.is_empty():
        return []
    prepared = add_time_bucket(games, period)
    grouped = prepared.group_by(["period", "champion"]).len().rename({"len": "games"})
    totals = prepared.group_by("period").len().rename({"len": "period_games"})
    return (
        grouped.join(totals, on="period")
        .with_columns((pl.col("games") * 100 / pl.col("period_games")).alias("pick_rate"))
        .sort(["period", "games"], descending=[True, True])
        .to_dicts()
    )


def role_distribution(games: pl.DataFrame, period: str = "patch") -> list[dict[str, object]]:
    if games.is_empty():
        return []
    prepared = add_time_bucket(games, period)
    grouped = prepared.group_by(["period", "role"]).len().rename({"len": "games"})
    totals = prepared.group_by("period").len().rename({"len": "period_games"})
    return (
        grouped.join(totals, on="period")
        .with_columns((pl.col("games") * 100 / pl.col("period_games")).alias("role_rate"))
        .sort(["period", "games"], descending=[True, True])
        .to_dicts()
    )


def champion_trends(games: pl.DataFrame, champion: str, bucket: str = "patch") -> list[dict[str, object]]:
    selected = games.filter(pl.col("champion") == champion)
    if selected.is_empty():
        return []
    prepared = add_time_bucket(selected, bucket)
    rows = []
    for period in prepared["period"].drop_nulls().unique().sort().to_list():
        group = prepared.filter(pl.col("period") == period)
        summary = analysis_summary(group)
        gold_diff = (
            group.select(pl.col("gold_diff_15").drop_nulls().mean()).item()
            if "gold_diff_15" in group.columns else None
        )
        rows.append({
            "period": period,
            "games": group.height,
            "winrate": float(summary["winrate"]),
            "kda": summary["kda"],
            "cs_per_min": summary["cs_per_minute"],
            "gold_diff_15": gold_diff,
        })
    return rows
