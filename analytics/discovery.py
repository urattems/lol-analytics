"""Automatic, sample-protected discovery of descriptive context gaps."""

from __future__ import annotations

import math

import polars as pl

from analytics.explorer import analysis_summary
from analytics.samples import sample_size_label


MIN_CANDIDATE_GAMES = 5
MIN_HIGHLIGHT_GAMES = 10


def interestingness_score(minimum_group: int, winrate_gap: float) -> float:
    """Rank internally as |WR gap| × sqrt(smaller N); never a probability."""

    return abs(winrate_gap) * math.sqrt(max(0, minimum_group))


def _prepare_candidates(games: pl.DataFrame) -> list[tuple[str, str]]:
    prepared: list[tuple[str, str]] = []
    dimensions = {
        "Side": "side",
        "Queue": "queue_id",
        "Patch": "patch",
        "Early @10": "gold_state_10",
        "Early @15": "gold_state_15",
        "First dragon": "first_dragon",
        "First death": "first_death_before_10",
        "Session position": "session_game_bucket",
        "Squad context": "with_recurring_teammate",
    }
    for label, column in dimensions.items():
        if column in games.columns:
            prepared.append((label, column))
    return prepared


def _duration_buckets(games: pl.DataFrame) -> pl.DataFrame:
    return games.with_columns(
        pl.when(pl.col("duration") < 1_200).then(pl.lit("<20 min"))
        .when(pl.col("duration") < 1_800).then(pl.lit("20–30 min"))
        .otherwise(pl.lit("30+ min"))
        .alias("discovery_duration")
    )


def discover_contexts(games: pl.DataFrame) -> list[dict[str, object]]:
    """Return strongest eligible max-vs-min group contrasts for each dimension."""

    if games.is_empty():
        return []
    prepared = _duration_buckets(games)
    candidates = [*_prepare_candidates(prepared), ("Duration", "discovery_duration")]
    if prepared.height >= MIN_CANDIDATE_GAMES * 2:
        ordered = prepared.sort(["game_creation", "match_id"], descending=[True, True])
        half = ordered.height // 2
        labels = ["Recent"] * half + ["Previous"] * (ordered.height - half)
        prepared = ordered.with_columns(pl.Series("discovery_period", labels))
        candidates.append(("Recent vs Previous", "discovery_period"))
    insights: list[dict[str, object]] = []
    for label, column in candidates:
        groups: list[dict[str, object]] = []
        for value in prepared[column].drop_nulls().unique().to_list():
            selected = prepared.filter(pl.col(column) == value)
            if selected.height < MIN_CANDIDATE_GAMES:
                continue
            summary = analysis_summary(selected)
            groups.append({
                "label": str(value),
                "games": selected.height,
                "winrate": float(summary["winrate"]),
                "kda": summary["kda"],
                "sample_size": sample_size_label(selected.height),
            })
        if len(groups) < 2:
            continue
        high = max(groups, key=lambda row: float(row["winrate"]))
        low = min(groups, key=lambda row: float(row["winrate"]))
        gap = float(high["winrate"]) - float(low["winrate"])
        minimum = min(int(high["games"]), int(low["games"]))
        insights.append({
            "context": label,
            "high": high,
            "low": low,
            "winrate_gap": gap,
            "interestingness_score": interestingness_score(minimum, gap),
            "promoted": min(int(high["games"]), int(low["games"])) >= MIN_HIGHLIGHT_GAMES,
            "statement": (
                "C’est l’un des écarts descriptifs les plus marqués dans la sélection actuelle."
            ),
        })
    return sorted(insights, key=lambda row: float(row["interestingness_score"]), reverse=True)


def promoted_insights(games: pl.DataFrame, limit: int = 5) -> list[dict[str, object]]:
    return [row for row in discover_contexts(games) if bool(row["promoted"])][:limit]
