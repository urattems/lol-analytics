"""Descriptive game-trajectory, objective and death-profile presets."""

from __future__ import annotations

import polars as pl

from analytics.explorer import analysis_summary
from analytics.samples import sample_size_label


def _mean(games: pl.DataFrame, column: str) -> float | None:
    if games.is_empty() or column not in games.columns:
        return None
    value = games.select(pl.col(column).drop_nulls().mean()).item()
    return float(value) if value is not None else None


def _metrics(games: pl.DataFrame) -> dict[str, object]:
    summary = analysis_summary(games)
    return {
        "games": games.height,
        "wins": int(summary["wins"]),
        "winrate": float(summary["winrate"]),
        "kda": summary["kda"],
        "gold_diff_15": _mean(games, "gold_diff_15"),
        "team_gold_diff_15": _mean(games, "team_gold_diff_15"),
        "sample_size": sample_size_label(games.height),
    }


def grouped_preset(games: pl.DataFrame, column: str) -> list[dict[str, object]]:
    if games.is_empty() or column not in games.columns:
        return []
    rows = []
    for value in games[column].drop_nulls().unique().to_list():
        selected = games.filter(pl.col(column) == value)
        rows.append({"label": value, **_metrics(selected)})
    return sorted(rows, key=lambda row: (-int(row["games"]), str(row["label"])))


def trajectory_preset(games: pl.DataFrame) -> list[dict[str, object]]:
    """Only complete @10/@15/@20 trajectories are grouped; short games stay N/A."""

    return grouped_preset(games, "trajectory")


def objective_context(games: pl.DataFrame) -> dict[str, list[dict[str, object]]]:
    return {
        "first_dragon": grouped_preset(games, "first_dragon"),
        "first_herald": grouped_preset(games, "first_herald"),
        "first_tower": grouped_preset(games, "first_tower"),
    }


def death_profile(games: pl.DataFrame) -> list[dict[str, object]]:
    rows = grouped_preset(games, "death_profile")
    order = {"0 deaths before 10": 0, "1 death before 10": 1, "2+ deaths before 10": 2}
    return sorted(rows, key=lambda row: order.get(str(row["label"]), 99))
