"""Final-inventory analytics without making purchase-order assumptions."""

from __future__ import annotations

import json
from typing import Any

import polars as pl

from analytics.overview import per_match_kda_expression, per_minute_expression


BUILD_SUMMARY_SCHEMA = {
    "build_key": pl.String,
    "items": pl.List(pl.Int64),
    "build_label": pl.String,
    "games": pl.UInt32,
    "wins": pl.Int64,
    "losses": pl.Int64,
    "winrate": pl.Float64,
    "kda": pl.Float64,
    "cs_per_minute": pl.Float64,
    "gold_per_minute": pl.Float64,
    "damage_per_minute": pl.Float64,
}


def parse_item_ids(
    value: str | list[int] | None, include_trinket: bool = True
) -> list[int]:
    """Parse item slots, optionally excluding Riot's seventh trinket slot."""

    if value is None:
        return []
    raw: Any = value
    if isinstance(value, str):
        try:
            raw = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    parsed: list[int] = []
    slots = raw if include_trinket else raw[:6]
    for item in slots:
        if isinstance(item, bool):
            continue
        try:
            item_id = int(item)
        except (TypeError, ValueError):
            continue
        if item_id > 0:
            parsed.append(item_id)
    return parsed


def trinket_item_id(value: str | list[int] | None) -> int | None:
    """Return Riot's seventh inventory slot without confusing it with the build."""

    raw: Any = value
    if isinstance(value, str):
        try:
            raw = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(raw, list) or len(raw) < 7:
        return None
    try:
        item_id = int(raw[6])
    except (TypeError, ValueError):
        return None
    return item_id if item_id > 0 else None


def canonical_build_key(value: str | list[int] | None) -> str:
    """Create a stable main-build key from item0..item5, excluding the trinket."""

    return json.dumps(
        sorted(parse_item_ids(value, include_trinket=False)), separators=(",", ":")
    )


def build_label(items: list[int] | pl.Series) -> str:
    """Create a readable fallback label when item metadata is unavailable."""

    values = items.to_list() if isinstance(items, pl.Series) else items
    return " · ".join(str(item_id) for item_id in values) if values else "Inventaire vide"


def filter_build_dataset(
    games: pl.DataFrame, champion: str | None = None, role: str | None = None
) -> pl.DataFrame:
    """Apply the deliberately small Build Lab champion and role filters."""

    filtered = games
    if champion:
        filtered = filtered.filter(pl.col("champion") == champion)
    if role:
        filtered = filtered.filter(pl.col("role") == role)
    return filtered


def build_summary(games: pl.DataFrame) -> pl.DataFrame:
    """Group equivalent final inventories and calculate factual observed outcomes."""

    if games.is_empty():
        return pl.DataFrame(schema=BUILD_SUMMARY_SCHEMA)
    required = {"items", "win", "kills", "deaths", "assists", "duration"}
    missing = required.difference(games.columns)
    if missing:
        raise ValueError(f"Missing build column(s): {', '.join(sorted(missing))}")
    frame = games
    for column in ("cs_total", "gold_earned", "damage_dealt"):
        if column not in frame.columns:
            frame = frame.with_columns(pl.lit(None, dtype=pl.Int64).alias(column))

    records = []
    for row in frame.select(sorted(required | {"cs_total", "gold_earned", "damage_dealt"})).to_dicts():
        items = sorted(parse_item_ids(row.get("items"), include_trinket=False))
        row["build_key"] = json.dumps(items, separators=(",", ":"))
        row["parsed_items"] = items
        records.append(row)
    prepared = pl.from_dicts(records, strict=False, infer_schema_length=None)
    return (
        prepared.group_by("build_key")
        .agg(
            pl.col("parsed_items").first().alias("items"),
            pl.len().alias("games"),
            (pl.col("win") == 1).sum().alias("wins"),
            (pl.col("win") == 0).sum().alias("losses"),
            ((pl.col("win") == 1).sum() * 100.0 / pl.len()).alias("winrate"),
            per_match_kda_expression().mean().alias("kda"),
            per_minute_expression("cs_total").mean().alias("cs_per_minute"),
            per_minute_expression("gold_earned").mean().alias("gold_per_minute"),
            per_minute_expression("damage_dealt").mean().alias("damage_per_minute"),
        )
        .with_columns(
            pl.col("items")
            .map_elements(build_label, return_dtype=pl.String)
            .alias("build_label")
        )
        .select(list(BUILD_SUMMARY_SCHEMA))
        .sort(["games", "build_label"], descending=[True, False])
    )


def compare_builds(
    summary: pl.DataFrame, build_a: str, build_b: str
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    """Return two observed summary rows without ranking or causal interpretation."""

    if summary.is_empty():
        return None, None

    def find(build_key: str) -> dict[str, object] | None:
        selected = summary.filter(pl.col("build_key") == build_key)
        return selected.row(0, named=True) if not selected.is_empty() else None

    return find(build_a), find(build_b)


def purchase_sequence_summary(
    timeline_rows: list[dict[str, object]], max_steps: int = 8
) -> list[dict[str, object]]:
    """Group exact observed purchase orders without classifying final or major items."""

    if max_steps < 1:
        raise ValueError("max_steps must be positive")
    grouped: dict[tuple[int, ...], list[dict[str, object]]] = {}
    for row in timeline_rows:
        purchases = row.get("purchases")
        if not isinstance(purchases, list):
            continue
        bought = [
            purchase
            for purchase in purchases
            if isinstance(purchase, dict)
            and purchase.get("event_type") == "ITEM_PURCHASED"
            and isinstance(purchase.get("item_id"), int)
        ][:max_steps]
        sequence = tuple(int(purchase["item_id"]) for purchase in bought)
        if sequence:
            grouped.setdefault(sequence, []).append({**row, "selected_purchases": bought})
    output: list[dict[str, object]] = []
    for sequence, rows in grouped.items():
        timings = []
        for index in range(len(sequence)):
            values = [
                int(row["selected_purchases"][index]["timestamp_ms"])  # type: ignore[index]
                for row in rows
                if row["selected_purchases"][index].get("timestamp_ms") is not None  # type: ignore[index]
            ]
            timings.append(sum(values) / len(values) if values else None)
        wins = sum(row.get("win") in {1, True} for row in rows)
        output.append(
            {
                "sequence": list(sequence),
                "games": len(rows),
                "wins": wins,
                "winrate": wins * 100.0 / len(rows),
                "average_timings_ms": timings,
            }
        )
    return sorted(output, key=lambda row: (-int(row["games"]), str(row["sequence"])))
