"""Parametric local match filtering shared by Explorer and AI Export."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import polars as pl
from pydantic import BaseModel, Field, model_validator

from analytics.constants import DEFAULT_INCLUDE_SHORT_GAMES, SHORT_GAME_THRESHOLD_SECONDS
from analytics.overview import get_overview_summary, per_minute_expression


class ExplorerSelection(BaseModel):
    """Single serializable selection contract shared by Explorer and exports."""

    base_game_limit: int | None = Field(default=20, ge=1)
    champion: str | None = None
    role: str | None = None
    queue_id: int | None = None
    patch: str | None = None
    side: Literal["blue", "red"] | None = None
    win: bool | None = None
    min_duration: int | None = Field(default=None, ge=0)
    max_duration: int | None = Field(default=None, ge=0)
    include_short_games: bool = DEFAULT_INCLUDE_SHORT_GAMES
    date_from_ms: int | None = Field(default=None, ge=0)
    date_to_ms: int | None = Field(default=None, ge=0)
    timeline_requirement: Literal["all", "available", "missing"] = "all"
    gold_diff_10_min: int | None = None
    gold_diff_10_max: int | None = None
    gold_diff_15_min: int | None = None
    gold_diff_15_max: int | None = None
    first_death_before_10: bool | None = None
    opponent_champion: str | None = None
    session_game_number: Literal["1", "2", "3", "4+"] | None = None
    recurring_teammate: str | None = None
    first_dragon: Literal["ours", "theirs", "none", "ambiguous"] | None = None
    trajectory: str | None = None

    @model_validator(mode="after")
    def normalize_ranges(self) -> ExplorerSelection:
        """Swap an inverted range so invalid UI input never crashes analysis."""

        if (
            self.min_duration is not None
            and self.max_duration is not None
            and self.min_duration > self.max_duration
        ):
            self.min_duration, self.max_duration = self.max_duration, self.min_duration
        for low_name, high_name in (
            ("date_from_ms", "date_to_ms"),
            ("gold_diff_10_min", "gold_diff_10_max"),
            ("gold_diff_15_min", "gold_diff_15_max"),
        ):
            low = getattr(self, low_name)
            high = getattr(self, high_name)
            if low is not None and high is not None and low > high:
                setattr(self, low_name, high)
                setattr(self, high_name, low)
        return self

    def active_filters(self) -> dict[str, object]:
        """Return only meaningful secondary filters for UI and exports."""

        values = self.model_dump(exclude={"base_game_limit"})
        return {
            key: value
            for key, value in values.items()
            if value is not None
            and not (key == "include_short_games" and value is True)
            and not (key == "timeline_requirement" and value == "all")
        }


# Kept as a public alias so V1/V2 callers and saved state remain valid.
AnalysisFilters = ExplorerSelection


@dataclass(frozen=True)
class AnalysisDataset:
    """Keep base-window and post-filter counts unambiguous."""

    base: pl.DataFrame
    filtered: pl.DataFrame
    filters: ExplorerSelection


def build_base_window(games: pl.DataFrame, limit: int | None) -> pl.DataFrame:
    """Select newest matches before any contextual filter is applied."""

    if games.is_empty():
        return games
    ordered = games.sort(["game_creation", "match_id"], descending=[True, True])
    return ordered.head(limit) if limit is not None else ordered


def build_analysis_dataset(games: pl.DataFrame, filters: ExplorerSelection) -> AnalysisDataset:
    """Apply the documented base-window-then-filters semantics."""

    base = build_base_window(games, filters.base_game_limit)
    filtered = base
    expressions: list[pl.Expr] = []
    if filters.champion is not None:
        expressions.append(pl.col("champion") == filters.champion)
    if filters.role is not None:
        expressions.append(pl.col("role") == filters.role)
    if filters.queue_id is not None:
        expressions.append(pl.col("queue_id") == filters.queue_id)
    if filters.patch is not None:
        expressions.append(pl.col("patch") == filters.patch)
    if filters.side is not None:
        expressions.append(pl.col("side") == filters.side)
    if filters.win is not None:
        expressions.append(pl.col("win") == int(filters.win))
    if filters.min_duration is not None:
        expressions.append(pl.col("duration").is_not_null() & (pl.col("duration") >= filters.min_duration))
    if filters.max_duration is not None:
        expressions.append(pl.col("duration").is_not_null() & (pl.col("duration") <= filters.max_duration))
    if not filters.include_short_games:
        expressions.append(
            pl.col("duration").is_null()
            | (pl.col("duration") >= SHORT_GAME_THRESHOLD_SECONDS)
        )
    if filters.date_from_ms is not None:
        expressions.append(pl.col("game_creation") >= filters.date_from_ms)
    if filters.date_to_ms is not None:
        expressions.append(pl.col("game_creation") <= filters.date_to_ms)

    def optional_column(column: str, expression: pl.Expr) -> None:
        # A requested contextual filter cannot match when its source fact is absent.
        expressions.append(expression if column in filtered.columns else pl.lit(False))

    if filters.timeline_requirement != "all":
        optional_column(
            "timeline_available",
            pl.col("timeline_available") == (filters.timeline_requirement == "available"),
        )
    for minute in (10, 15):
        column = f"gold_diff_{minute}"
        low = getattr(filters, f"gold_diff_{minute}_min")
        high = getattr(filters, f"gold_diff_{minute}_max")
        if low is not None:
            optional_column(column, pl.col(column).is_not_null() & (pl.col(column) >= low))
        if high is not None:
            optional_column(column, pl.col(column).is_not_null() & (pl.col(column) <= high))
    if filters.first_death_before_10 is not None:
        optional_column(
            "first_death_before_10",
            pl.col("first_death_before_10") == filters.first_death_before_10,
        )
    if filters.opponent_champion is not None:
        optional_column(
            "opponent_champion",
            pl.col("opponent_champion") == filters.opponent_champion,
        )
    if filters.session_game_number is not None:
        optional_column(
            "session_game_bucket",
            pl.col("session_game_bucket") == filters.session_game_number,
        )
    if filters.recurring_teammate is not None:
        optional_column(
            "recurring_teammates",
            pl.col("recurring_teammates").list.contains(filters.recurring_teammate),
        )
    if filters.first_dragon is not None:
        optional_column("first_dragon", pl.col("first_dragon") == filters.first_dragon)
    if filters.trajectory is not None:
        optional_column("trajectory", pl.col("trajectory") == filters.trajectory)
    if expressions:
        combined = expressions[0]
        for expression in expressions[1:]:
            combined &= expression
        filtered = filtered.filter(combined)
    return AnalysisDataset(base=base, filtered=filtered, filters=filters)


def analysis_summary(games: pl.DataFrame) -> dict[str, float | int | None]:
    """Return the extended KPI set used by Explorer and exports."""

    summary = get_overview_summary(games)
    if games.is_empty():
        summary["gold_per_minute"] = 0.0
        summary["vision_per_minute"] = 0.0
        return summary
    for column in ("gold_earned", "vision_score"):
        if column not in games.columns:
            value = None
        else:
            value = games.select(per_minute_expression(column).mean()).item()
        summary[f"{column.removesuffix('_earned').removesuffix('_score')}_per_minute"] = (
            float(value) if value is not None else None
        )
    return summary


def filter_matches(games: pl.DataFrame, champion: str | None = None) -> pl.DataFrame:
    """Filter games without coupling analytical logic to Streamlit state."""

    return build_analysis_dataset(
        games, AnalysisFilters(base_game_limit=None, champion=champion)
    ).filtered
