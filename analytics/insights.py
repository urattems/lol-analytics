"""Descriptive preset analytics with explicit sample sizes and timeline coverage."""

from __future__ import annotations

from typing import Callable

import polars as pl

from analytics.constants import LANE_GOLD_LEAD_THRESHOLD, TEAM_GOLD_LEAD_THRESHOLD
from analytics.consistency import consistency_summary
from analytics.discovery import discover_contexts, promoted_insights
from analytics.explorer import analysis_summary
from analytics.game_flow import death_profile, objective_context, trajectory_preset
from analytics.overview import per_minute_expression
from analytics.personal_meta import champion_frequency, role_distribution
from analytics.sessions import session_analytics
from analytics.samples import sample_size_label
from core.queues import queue_name


def sample_confidence(n: int) -> str:
    """Return a plain-language sample-size label, not statistical confidence."""

    return sample_size_label(n)


def _mean(frame: pl.DataFrame, column: str) -> float | None:
    if frame.is_empty() or column not in frame.columns:
        return None
    value = frame.select(pl.col(column).drop_nulls().mean()).item()
    return float(value) if value is not None else None


def _metrics(frame: pl.DataFrame) -> dict[str, object]:
    summary = analysis_summary(frame)
    row: dict[str, object] = {
        "games": frame.height,
        "wins": int(summary["wins"]),
        "winrate": float(summary["winrate"]),
        "kda": summary["kda"],
        "cs_per_min": summary["cs_per_minute"],
        "gold_per_min": summary["gold_per_minute"],
        "damage_per_min": summary["damage_per_minute"],
        "vision_per_min": summary["vision_per_minute"],
        "sample_size": sample_confidence(frame.height),
    }
    for column in (
        "gold_10", "gold_15", "cs_10", "cs_15", "gold_diff_10",
        "gold_diff_15", "team_gold_diff_15", "deaths_before_10",
    ):
        row[column] = _mean(frame, column)
    row["timeline_games"] = (
        frame.filter(pl.col("timeline_available") == True).height  # noqa: E712
        if "timeline_available" in frame.columns else 0
    )
    return row


def join_timeline(games: pl.DataFrame, timeline: pl.DataFrame) -> pl.DataFrame:
    """Left-join scalar timeline facts so missing timelines never drop a match."""

    if games.is_empty():
        return games
    if timeline.is_empty() or "match_id" not in timeline.columns:
        return (
            games
            if "timeline_available" in games.columns
            else games.with_columns(pl.lit(False).alias("timeline_available"))
        )
    extra = [column for column in timeline.columns if column == "match_id" or column not in games.columns]
    joined = games.join(timeline.select(extra), on="match_id", how="left")
    if "timeline_available" not in joined.columns:
        return joined.with_columns(pl.lit(False).alias("timeline_available"))
    return joined.with_columns(pl.col("timeline_available").fill_null(False))


def _group_rows(
    games: pl.DataFrame,
    column: str,
    label: Callable[[object], str] = str,
) -> list[dict[str, object]]:
    if games.is_empty() or column not in games.columns:
        return []
    rows = []
    for value in games.get_column(column).drop_nulls().unique().to_list():
        row = {"label": label(value), **_metrics(games.filter(pl.col(column) == value))}
        rows.append(row)
    return sorted(rows, key=lambda row: (-int(row["games"]), str(row["label"])))


def side_preset(games: pl.DataFrame) -> list[dict[str, object]]:
    return _group_rows(games, "side", lambda value: f"{str(value).upper()} SIDE")


def role_preset(games: pl.DataFrame) -> list[dict[str, object]]:
    return _group_rows(games, "role")


def duration_preset(games: pl.DataFrame) -> list[dict[str, object]]:
    if games.is_empty():
        return []
    prepared = games.with_columns(
        pl.when(pl.col("duration") < 1_200).then(pl.lit("<20 min"))
        .when(pl.col("duration") < 1_500).then(pl.lit("20–25"))
        .when(pl.col("duration") < 1_800).then(pl.lit("25–30"))
        .when(pl.col("duration") < 2_100).then(pl.lit("30–35"))
        .otherwise(pl.lit("35+"))
        .alias("duration_bucket")
    )
    order = {"<20 min": 0, "20–25": 1, "25–30": 2, "30–35": 3, "35+": 4}
    return sorted(_group_rows(prepared, "duration_bucket"), key=lambda row: order[str(row["label"])])


def fast_wins_preset(games: pl.DataFrame) -> dict[str, object]:
    selected = games.filter((pl.col("win") == 1) & (pl.col("duration") < 1_200))
    return {"label": "Victoires <20 min", **_metrics(selected)}


def wins_losses_preset(games: pl.DataFrame) -> list[dict[str, object]]:
    return [
        {"label": label, **_metrics(games.filter(pl.col("win") == value))}
        for value, label in ((1, "Victoires"), (0, "Défaites"))
    ]


def queue_preset(games: pl.DataFrame) -> list[dict[str, object]]:
    return _group_rows(games, "queue_id", lambda value: queue_name(int(value)))


def patch_preset(games: pl.DataFrame) -> list[dict[str, object]]:
    rows = _group_rows(games, "patch")
    return sorted(rows, key=lambda row: str(row["label"]), reverse=True)


def recent_previous_preset(games: pl.DataFrame, size: int = 20) -> list[dict[str, object]]:
    if size < 1:
        raise ValueError("size must be positive")
    ordered = games.sort(["game_creation", "match_id"], descending=[True, True])
    return [
        {"label": f"{size} dernières", **_metrics(ordered.head(size))},
        {"label": f"{size} précédentes", **_metrics(ordered.slice(size, size))},
    ]


def early_lead_preset(games: pl.DataFrame, minute: int) -> list[dict[str, object]]:
    column = f"gold_diff_{minute}"
    if column not in games.columns:
        return []
    covered = games.filter(pl.col(column).is_not_null())
    groups = (
        ("Ahead", pl.col(column) > LANE_GOLD_LEAD_THRESHOLD),
        ("Even", pl.col(column).is_between(-LANE_GOLD_LEAD_THRESHOLD, LANE_GOLD_LEAD_THRESHOLD, closed="both")),
        ("Behind", pl.col(column) < -LANE_GOLD_LEAD_THRESHOLD),
    )
    return [{"label": label, **_metrics(covered.filter(expression))} for label, expression in groups]


def conversion_preset(games: pl.DataFrame) -> dict[str, object]:
    selected = games.filter(
        pl.col("team_gold_diff_15").is_not_null()
        & (pl.col("team_gold_diff_15") > TEAM_GOLD_LEAD_THRESHOLD)
    ) if "team_gold_diff_15" in games.columns else games.head(0)
    return {"label": "Conversion d’avance", **_metrics(selected)}


def comeback_preset(games: pl.DataFrame) -> dict[str, object]:
    selected = games.filter(
        pl.col("team_gold_diff_15").is_not_null()
        & (pl.col("team_gold_diff_15") < -TEAM_GOLD_LEAD_THRESHOLD)
    ) if "team_gold_diff_15" in games.columns else games.head(0)
    return {"label": "Victoire depuis retard @15", **_metrics(selected)}


def first_death_preset(games: pl.DataFrame) -> list[dict[str, object]]:
    if "first_death_before_10" not in games.columns:
        return []
    covered = games.filter(pl.col("timeline_available") == True)  # noqa: E712
    return [
        {"label": label, **_metrics(covered.filter(pl.col("first_death_before_10") == value))}
        for value, label in ((True, "Mort avant 10"), (False, "Pas de mort avant 10"))
    ]


def build_insight_bundle(games: pl.DataFrame, timeline: pl.DataFrame) -> dict[str, object]:
    """Build every V2 preset from one selected match window."""

    enriched = join_timeline(games, timeline)
    timeline_games = enriched.filter(pl.col("timeline_available") == True).height  # noqa: E712
    objectives = objective_context(enriched)
    return {
        "games": enriched,
        "coverage": {"available": timeline_games, "selected": enriched.height},
        "side": side_preset(enriched),
        "role": role_preset(enriched),
        "duration": duration_preset(enriched),
        "fast_wins": fast_wins_preset(enriched),
        "wins_losses": wins_losses_preset(enriched),
        "queue": queue_preset(enriched),
        "patch": patch_preset(enriched),
        "recent_previous": recent_previous_preset(enriched),
        "ahead_10": early_lead_preset(enriched, 10),
        "ahead_15": early_lead_preset(enriched, 15),
        "conversion": conversion_preset(enriched),
        "comeback": comeback_preset(enriched),
        "first_death": first_death_preset(enriched),
        "trajectories": trajectory_preset(enriched),
        "first_dragon": objectives["first_dragon"],
        "first_herald": objectives["first_herald"],
        "first_tower": objectives["first_tower"],
        "death_profile": death_profile(enriched),
        "personal_meta": {
            "champion_frequency": champion_frequency(enriched, "patch"),
            "role_distribution": role_distribution(enriched, "patch"),
        },
        "consistency": consistency_summary(enriched),
        "sessions": session_analytics(enriched),
        "discovery": discover_contexts(enriched),
        "highlights": promoted_insights(enriched),
    }
