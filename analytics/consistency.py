"""Robust distribution summaries without arbitrary composite scores."""

from __future__ import annotations

import polars as pl

from analytics.overview import per_match_kda_expression, per_minute_expression


METRICS = {
    "KDA": per_match_kda_expression(),
    "CS/min": per_minute_expression("cs_total"),
    "DPM": per_minute_expression("damage_dealt"),
    "Gold/min": per_minute_expression("gold_earned"),
    "Gold Diff @15": pl.col("gold_diff_15"),
}


def consistency_summary(games: pl.DataFrame) -> list[dict[str, object]]:
    """Return mean, median, P25, P75 and IQR for available per-game values."""

    rows: list[dict[str, object]] = []
    for label, expression in METRICS.items():
        if label == "Gold Diff @15" and "gold_diff_15" not in games.columns:
            continue
        if games.is_empty():
            values = pl.Series([], dtype=pl.Float64)
        else:
            values = games.select(expression.alias("value"))["value"].drop_nulls()
        p25 = float(values.quantile(0.25, interpolation="linear")) if len(values) else None
        p75 = float(values.quantile(0.75, interpolation="linear")) if len(values) else None
        rows.append({
            "metric": label,
            "games": len(values),
            "mean": float(values.mean()) if len(values) else None,
            "median": float(values.median()) if len(values) else None,
            "p25": p25,
            "p75": p75,
            "iqr": p75 - p25 if p25 is not None and p75 is not None else None,
        })
    return rows
