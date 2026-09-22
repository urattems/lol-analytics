"""Fundamental player metrics calculated with Polars."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import polars as pl

from analytics.constants import SHORT_GAME_THRESHOLD_SECONDS
from core.db import Database


GameData = pl.DataFrame | Sequence[Mapping[str, Any]]
PLAYER_MATCH_SCHEMA = {
    "match_id": pl.String,
    "patch": pl.String,
    "queue_id": pl.Int64,
    "duration": pl.Int64,
    "game_creation": pl.Int64,
    "champion": pl.String,
    "role": pl.String,
    "win": pl.Int64,
    "kills": pl.Int64,
    "deaths": pl.Int64,
    "assists": pl.Int64,
    "gold_earned": pl.Int64,
    "cs_total": pl.Int64,
    "damage_dealt": pl.Int64,
    "damage_share": pl.Float64,
    "vision_score": pl.Int64,
    "items": pl.String,
    "side": pl.String,
}


def _frame(games: GameData) -> pl.DataFrame:
    return games if isinstance(games, pl.DataFrame) else pl.DataFrame(games)


def _sum(frame: pl.DataFrame, column: str) -> float:
    if frame.is_empty():
        return 0.0
    if column not in frame.columns:
        raise ValueError(f"Missing analytics column: {column}")
    value = frame.select(pl.col(column).fill_null(0).sum()).item()
    return float(value or 0)


def _require_columns(frame: pl.DataFrame, columns: tuple[str, ...]) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"Missing analytics column(s): {', '.join(missing)}")


def per_match_kda_expression() -> pl.Expr:
    complete = pl.all_horizontal(
        pl.col("kills").is_not_null(),
        pl.col("assists").is_not_null(),
        pl.col("deaths").is_not_null(),
    )
    return (
        pl.when(complete)
        .then(
            (pl.col("kills") + pl.col("assists"))
            / pl.max_horizontal(pl.col("deaths"), pl.lit(1))
        )
        .otherwise(None)
    )


def per_minute_expression(value_column: str) -> pl.Expr:
    valid = (
        pl.col(value_column).is_not_null()
        & pl.col("duration").is_not_null()
        & (pl.col("duration") > 0)
    )
    return (
        pl.when(valid)
        .then(pl.col(value_column) / (pl.col("duration") / 60.0))
        .otherwise(None)
    )


def get_player_matches(database: Database, puuid: str, limit: int | None) -> pl.DataFrame:
    """Load the player's newest matches from SQLite into a typed Polars dataset."""

    rows = database.player_matches(puuid, limit)
    if not rows:
        return pl.DataFrame(schema=PLAYER_MATCH_SCHEMA)
    return pl.from_dicts(rows, schema=PLAYER_MATCH_SCHEMA, strict=False).with_columns(
        pl.when(pl.col("game_creation").is_between(0, 253370764800000))
        .then(pl.col("game_creation")).otherwise(None).alias("game_creation")
    )


def game_count(games: GameData) -> int:
    """Return the number of games in the dataset."""

    return _frame(games).height


def wins(games: GameData) -> int:
    """Count truthy win values."""

    frame = _frame(games)
    if frame.is_empty():
        return 0
    if "win" not in frame.columns:
        raise ValueError("Missing analytics column: win")
    return int(frame.select(pl.col("win").cast(pl.Int64).fill_null(0).sum()).item() or 0)


def losses(games: GameData) -> int:
    """Count explicit losses without treating missing outcomes as losses."""

    frame = _frame(games)
    if frame.is_empty():
        return 0
    _require_columns(frame, ("win",))
    return frame.filter(pl.col("win") == False).height  # noqa: E712


def winrate(games: GameData) -> float:
    """Return win rate as a percentage from 0 to 100."""

    total = game_count(games)
    return 0.0 if total == 0 else wins(games) * 100.0 / total


def kda(games: GameData) -> float | None:
    """Average per-match ``(kills + assists) / max(1, deaths)`` values."""

    frame = _frame(games)
    if frame.is_empty():
        return 0.0
    _require_columns(frame, ("kills", "assists", "deaths"))
    value = frame.select(per_match_kda_expression().mean()).item()
    return float(value) if value is not None else None


def _per_minute(games: GameData, value_column: str) -> float | None:
    frame = _frame(games)
    if frame.is_empty():
        return 0.0
    _require_columns(frame, (value_column, "duration"))
    value = frame.select(per_minute_expression(value_column).mean()).item()
    return float(value) if value is not None else None


def cs_per_minute(games: GameData) -> float | None:
    """Calculate the mean of per-match CS/min values."""

    return _per_minute(games, "cs_total")


def damage_per_minute(games: GameData) -> float | None:
    """Calculate the mean of per-match champion damage/min values."""

    return _per_minute(games, "damage_dealt")


def overview_metrics(games: GameData) -> dict[str, float | int | None]:
    """Return all foundational overview values in a presentation-neutral mapping."""

    return {
        "games": game_count(games),
        "wins": wins(games),
        "losses": losses(games),
        "winrate": winrate(games),
        "kda": kda(games),
        "cs_per_minute": cs_per_minute(games),
        "damage_per_minute": damage_per_minute(games),
    }


def get_overview_summary(games: GameData) -> dict[str, float | int | None]:
    """Return KPI values plus the number of included sub-five-minute matches."""

    frame = _frame(games)
    summary = overview_metrics(frame)
    if frame.is_empty():
        summary["short_games"] = 0
    else:
        _require_columns(frame, ("duration",))
        summary["short_games"] = frame.filter(
            pl.col("duration").is_not_null()
            & (pl.col("duration") < SHORT_GAME_THRESHOLD_SECONDS)
        ).height
    return summary


def champion_summary(games: GameData) -> pl.DataFrame:
    """Aggregate player performance by champion and sort by game count."""

    frame = _frame(games)
    output_schema = {
        "champion": pl.String,
        "games": pl.UInt32,
        "wins": pl.Int64,
        "losses": pl.Int64,
        "winrate": pl.Float64,
        "kda": pl.Float64,
        "cs_per_minute": pl.Float64,
        "damage_per_minute": pl.Float64,
        "gold_per_minute": pl.Float64,
        "vision_per_minute": pl.Float64,
        "average_kills": pl.Float64,
        "average_deaths": pl.Float64,
        "average_assists": pl.Float64,
        "primary_role": pl.String,
    }
    if frame.is_empty():
        return pl.DataFrame(schema=output_schema)
    optional_columns = {
        "cs_total": pl.Int64,
        "damage_dealt": pl.Int64,
        "gold_earned": pl.Int64,
        "vision_score": pl.Int64,
        "role": pl.String,
    }
    missing_optional = [
        pl.lit(None, dtype=data_type).alias(column)
        for column, data_type in optional_columns.items()
        if column not in frame.columns
    ]
    if missing_optional:
        frame = frame.with_columns(missing_optional)
    _require_columns(
        frame,
        (
            "champion",
            "win",
            "kills",
            "deaths",
            "assists",
            "duration",
            "cs_total",
            "damage_dealt",
            "gold_earned",
            "vision_score",
            "role",
        ),
    )
    grouped = (
        frame.group_by("champion")
        .agg(
            pl.len().alias("games"),
            (pl.col("win") == 1).sum().alias("wins"),
            (pl.col("win") == 0).sum().alias("losses"),
            ((pl.col("win") == 1).sum() * 100.0 / pl.len()).alias("winrate"),
            per_match_kda_expression().mean().alias("kda"),
            per_minute_expression("cs_total").mean().alias("cs_per_minute"),
            per_minute_expression("damage_dealt").mean().alias("damage_per_minute"),
            per_minute_expression("gold_earned").mean().alias("gold_per_minute"),
            per_minute_expression("vision_score").mean().alias("vision_per_minute"),
            pl.col("kills").mean().alias("average_kills"),
            pl.col("deaths").mean().alias("average_deaths"),
            pl.col("assists").mean().alias("average_assists"),
        )
    )
    primary_roles = (
        frame.with_columns(pl.col("role").fill_null("UNKNOWN"))
        .group_by(["champion", "role"])
        .len(name="role_games")
        .sort(["champion", "role_games", "role"], descending=[False, True, False])
        .group_by("champion", maintain_order=True)
        .first()
        .select("champion", pl.col("role").alias("primary_role"))
    )
    return grouped.join(primary_roles, on="champion", how="left").sort(
        ["games", "champion"], descending=[True, False]
    )


def rolling_window_size(game_total: int) -> int:
    """Select the documented rolling window for the current dataset size."""

    if game_total <= 20:
        return 5
    if game_total <= 50:
        return 10
    return 20


def rolling_winrate(games: GameData) -> tuple[pl.DataFrame, int]:
    """Calculate only complete rolling windows from oldest to newest match."""

    frame = _frame(games)
    window = rolling_window_size(frame.height)
    if frame.is_empty():
        return frame, window
    _require_columns(frame, ("game_creation", "match_id", "win"))
    chronological = frame.sort(["game_creation", "match_id"])
    prepared = chronological.with_row_index("match_number", offset=1).with_columns(
        pl.col("win").cast(pl.Int64)
        .rolling_sum(window_size=window, min_samples=window)
        .alias("rolling_wins")
    ).with_columns(
        (pl.col("rolling_wins") * 100.0 / window).alias("rolling_winrate"),
        (pl.lit(window) - pl.col("rolling_wins")).alias("rolling_losses"),
    )
    return prepared.filter(pl.col("rolling_winrate").is_not_null()), window


def recent_matches(games: GameData, count: int = 5) -> pl.DataFrame:
    """Return the newest rows explicitly ordered for list presentation."""

    if count < 1:
        raise ValueError("count must be positive")
    frame = _frame(games)
    if frame.is_empty():
        return frame
    _require_columns(frame, ("game_creation", "match_id"))
    return frame.sort(["game_creation", "match_id"], descending=[True, True]).head(count)
