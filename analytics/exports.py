"""Deterministic, factual JSON and Markdown exports for external analysis."""

from __future__ import annotations

import json
import html
import math
import re
import unicodedata
from collections.abc import Callable
from datetime import datetime
from typing import Any

import polars as pl

from analytics.builds import parse_item_ids, trinket_item_id
from analytics.explorer import AnalysisFilters, analysis_summary, build_analysis_dataset
from analytics.overview import champion_summary
from core.queues import queue_name
from core.static_data import ItemStaticData


SCHEMA_VERSION = "2.1"
EXPORT_NOTE = (
    "Ce document est un export de données de parties League of Legends. "
    "Les statistiques décrivent uniquement la sélection indiquée. "
    "Elles ne démontrent pas de relation causale entre build, champion ou résultat."
)


def to_csv(games: pl.DataFrame) -> str:
    """Serialize a dataset for backwards compatibility with the data foundation."""

    # Quoting alone does not stop spreadsheet formula execution.
    strings = [name for name, dtype in games.schema.items() if dtype == pl.String]
    safe = games.with_columns([
        pl.when(pl.col(name).str.contains(r"^[\s]*[=+@-]|^[\t\r\n]"))
        .then(pl.lit("'") + pl.col(name)).otherwise(pl.col(name)).alias(name)
        for name in strings
    ])
    return safe.write_csv()


def markdown_text(value: object) -> str:
    """Render untrusted labels as literal text, not HTML or Markdown syntax."""
    text = html.escape(str(value)).replace("\r", " ").replace("\n", " ")
    return re.sub(r"([\\`*_\[\]()#!|])", r"\\\1", text)


def _markdown_values(value: Any) -> Any:
    if isinstance(value, str):
        return markdown_text(value)
    if isinstance(value, dict):
        return {key: _markdown_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_markdown_values(item) for item in value]
    return value


def _safe_number(value: Any) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rounded(value: Any, digits: int = 4) -> int | float | None:
    number = _safe_number(value)
    if number is None or isinstance(number, int):
        return number
    return round(number, digits)


def _per_minute(value: Any, duration: Any) -> float | None:
    numeric_value = _safe_number(value)
    numeric_duration = _safe_number(duration)
    if numeric_value is None or numeric_duration is None or numeric_duration <= 0:
        return None
    return round(float(numeric_value) / (float(numeric_duration) / 60.0), 4)


def _match_kda(kills: Any, deaths: Any, assists: Any) -> float | None:
    values = [_safe_number(value) for value in (kills, deaths, assists)]
    if any(value is None for value in values):
        return None
    safe_kills, safe_deaths, safe_assists = (float(value) for value in values if value is not None)
    return round((safe_kills + safe_assists) / max(1.0, safe_deaths), 4)


def _local_iso(timestamp_ms: Any) -> str | None:
    timestamp = _safe_number(timestamp_ms)
    if timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(float(timestamp) / 1000).astimezone().replace(microsecond=0).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _generated_iso(generated_at: datetime | None) -> str:
    value = generated_at or datetime.now().astimezone()
    if value.tzinfo is None:
        value = value.astimezone()
    return value.replace(microsecond=0).isoformat()


def _duration_label(seconds: Any) -> str | None:
    duration = _safe_number(seconds)
    if duration is None:
        return None
    minutes, remaining_seconds = divmod(max(0, int(duration)), 60)
    return f"{minutes:02d}:{remaining_seconds:02d}"


def _summary_payload(games: pl.DataFrame) -> dict[str, int | float | None]:
    summary = analysis_summary(games)
    return {
        "games": int(summary["games"]),
        "wins": int(summary["wins"]),
        "losses": int(summary["losses"]),
        "winrate": _rounded(summary["winrate"]),
        "kda": _rounded(summary["kda"]),
        "cs_per_min": _rounded(summary["cs_per_minute"]),
        "gold_per_min": _rounded(summary["gold_per_minute"]),
        "damage_per_min": _rounded(summary["damage_per_minute"]),
        "vision_per_min": _rounded(summary["vision_per_minute"]),
    }


def _champion_payload(games: pl.DataFrame) -> list[dict[str, object]]:
    columns = {
        "champion": "champion",
        "games": "games",
        "wins": "wins",
        "losses": "losses",
        "winrate": "winrate",
        "kda": "kda",
        "cs_per_minute": "cs_per_min",
        "gold_per_minute": "gold_per_min",
        "damage_per_minute": "damage_per_min",
        "vision_per_minute": "vision_per_min",
        "primary_role": "primary_role",
    }
    records: list[dict[str, object]] = []
    for row in champion_summary(games).to_dicts():
        record: dict[str, object] = {}
        for source, target in columns.items():
            value = row.get(source)
            record[target] = value if isinstance(value, str) else _rounded(value)
        records.append(record)
    return records


def _match_payload(
    games: pl.DataFrame,
    item_resolver: Callable[[int], ItemStaticData] | None = None,
    timeline_match_ids: set[str] | None = None,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for row in games.to_dicts():
        duration = row.get("duration")
        win_value = row.get("win")
        item_ids = parse_item_ids(row.get("items"), include_trinket=False)
        trinket_id = trinket_item_id(row.get("items"))
        items: list[object] = item_ids
        trinket: object = trinket_id
        if item_resolver:
            items = [
                {"id": item_id, "name": item_resolver(item_id).display_name}
                for item_id in item_ids
            ]
            trinket = (
                {"id": trinket_id, "name": item_resolver(trinket_id).display_name}
                if trinket_id is not None
                else None
            )
        match_id = str(row.get("match_id") or "")
        records.append(
            {
                "match_id": row.get("match_id"),
                "date": _local_iso(row.get("game_creation")),
                "patch": row.get("patch"),
                "queue_id": _safe_number(row.get("queue_id")),
                "queue_name": queue_name(row.get("queue_id")),
                "duration_seconds": _safe_number(duration),
                "duration_formatted": _duration_label(duration),
                "short_game": bool(duration is not None and int(duration) < 300),
                "champion": row.get("champion"),
                "role": row.get("role"),
                "side": row.get("side"),
                "win": bool(win_value) if win_value is not None else None,
                "kills": _safe_number(row.get("kills")),
                "deaths": _safe_number(row.get("deaths")),
                "assists": _safe_number(row.get("assists")),
                "kda": _match_kda(row.get("kills"), row.get("deaths"), row.get("assists")),
                "cs": _safe_number(row.get("cs_total")),
                "cs_per_min": _per_minute(row.get("cs_total"), duration),
                "gold": _safe_number(row.get("gold_earned")),
                "gold_per_min": _per_minute(row.get("gold_earned"), duration),
                "damage_to_champions": _safe_number(row.get("damage_dealt")),
                "damage_per_min": _per_minute(row.get("damage_dealt"), duration),
                "damage_share": _rounded(row.get("damage_share")),
                "vision_score": _safe_number(row.get("vision_score")),
                "vision_per_min": _per_minute(row.get("vision_score"), duration),
                "items": items,
                "trinket": trinket,
                "timeline_available": match_id in (timeline_match_ids or set()),
                "opponent_champion": row.get("opponent_champion"),
                "session_id": row.get("session_id"),
                "session_game_number": _safe_number(row.get("session_game_number")),
                "first_dragon": row.get("first_dragon"),
                "first_herald": row.get("first_herald"),
                "first_tower": row.get("first_tower"),
                "trajectory": row.get("trajectory"),
                "gold_diff_10": _safe_number(row.get("gold_diff_10")),
                "gold_diff_15": _safe_number(row.get("gold_diff_15")),
                "cs_diff_10": _safe_number(row.get("cs_diff_10")),
                "cs_diff_15": _safe_number(row.get("cs_diff_15")),
                "xp_diff_15": _safe_number(row.get("xp_diff_15")),
                "deaths_before_10": _safe_number(row.get("deaths_before_10")),
            }
        )
    return records


def build_export_payload(
    games: pl.DataFrame,
    filters: AnalysisFilters,
    riot_id: str,
    platform_region: str,
    routing_region: str,
    generated_at: datetime | None = None,
    item_resolver: Callable[[int], ItemStaticData] | None = None,
    timeline_match_ids: set[str] | None = None,
) -> dict[str, object]:
    """Build a filtered export with the exact Explorer selection semantics."""

    selection = build_analysis_dataset(games, filters)
    filtered = selection.filtered
    timestamps = (
        filtered.get_column("game_creation").drop_nulls().to_list()
        if "game_creation" in filtered.columns
        else []
    )
    date_range = {
        "from": _local_iso(min(timestamps)) if timestamps else None,
        "to": _local_iso(max(timestamps)) if timestamps else None,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _generated_iso(generated_at),
        "player": {
            "riot_id": riot_id,
            "platform_region": platform_region.upper(),
            "routing_region": routing_region.upper(),
        },
        "selection": {
            "mode": "filtered",
            "local_games": games.height,
            "base_game_limit": filters.base_game_limit,
            "games_in_base_window": selection.base.height,
            "games_after_filters": filtered.height,
            "filters": filters.active_filters(),
            "date_range": date_range,
        },
        "summary": _summary_payload(filtered),
        "champions": _champion_payload(filtered),
        "matches": _match_payload(filtered, item_resolver, timeline_match_ids),
    }


def build_full_export_payload(
    games: pl.DataFrame,
    riot_id: str,
    platform_region: str,
    routing_region: str,
    generated_at: datetime | None = None,
    item_resolver: Callable[[int], ItemStaticData] | None = None,
    timeline_match_ids: set[str] | None = None,
) -> dict[str, object]:
    """Export every local match, independent of any UI or Explorer state."""

    payload = build_export_payload(
        games,
        AnalysisFilters(base_game_limit=None, include_short_games=True),
        riot_id,
        platform_region,
        routing_region,
        generated_at,
        item_resolver,
        timeline_match_ids,
    )
    selection = payload["selection"]
    assert isinstance(selection, dict)
    selection.update(
        mode="full",
        local_games=games.height,
        games_in_base_window=games.height,
        games_after_filters=games.height,
        filters={},
    )
    if len(payload["matches"]) != games.height:  # type: ignore[arg-type]
        raise ValueError("Full export completeness check failed.")
    return payload


def export_json(payload: dict[str, object]) -> str:
    """Serialize readable UTF-8 JSON and reject invalid NaN values."""

    return json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def _metric(value: object, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "N/A"
    if isinstance(value, (int, float)):
        return f"{float(value):.{digits}f}{suffix}"
    return str(value)


def export_markdown(payload: dict[str, object]) -> str:
    """Render a neutral Markdown document suitable for an LLM conversation."""

    payload = _markdown_values(payload)
    player = payload["player"]
    selection = payload["selection"]
    summary = payload["summary"]
    champions = payload["champions"]
    matches = payload["matches"]
    assert isinstance(player, dict)
    assert isinstance(selection, dict)
    assert isinstance(summary, dict)
    assert isinstance(champions, list)
    assert isinstance(matches, list)
    date_range = selection["date_range"]
    assert isinstance(date_range, dict)
    filters = selection["filters"]
    filter_text = ", ".join(f"{key}={value}" for key, value in filters.items()) if isinstance(filters, dict) and filters else "Aucun filtre secondaire"
    lines = [
        "# LoL Analytics Export",
        "",
        EXPORT_NOTE,
        "",
        "## Export metadata",
        "",
        f"- Player: {player.get('riot_id', 'N/A')}",
        f"- Platform region: {player.get('platform_region', 'N/A')}",
        f"- Routing region: {player.get('routing_region', 'N/A')}",
        f"- Generated: {payload.get('generated_at', 'N/A')}",
        f"- Local games: {selection.get('local_games', selection.get('games_in_base_window', 0))}",
        f"- Games exported: {selection.get('games_after_filters', 0)}",
        f"- Base window: {selection.get('base_game_limit') if selection.get('base_game_limit') is not None else 'All local games'} ({selection.get('games_in_base_window', 0)} available)",
        f"- Period: {date_range.get('from') or 'N/A'} → {date_range.get('to') or 'N/A'}",
        f"- Filters: {filter_text}",
        "",
        "## Global Summary",
        "",
        f"- Games: {summary.get('games', 0)}",
        f"- Wins / Losses: {summary.get('wins', 0)} / {summary.get('losses', 0)}",
        f"- Winrate: {_metric(summary.get('winrate'), 1, ' %')}",
        f"- KDA: {_metric(summary.get('kda'))}",
        f"- CS/min: {_metric(summary.get('cs_per_min'))}",
        f"- Gold/min: {_metric(summary.get('gold_per_min'), 1)}",
        f"- Damage/min: {_metric(summary.get('damage_per_min'), 1)}",
        f"- Vision/min: {_metric(summary.get('vision_per_min'))}",
        "",
        "## Champion Summary",
        "",
    ]
    if champions:
        lines.extend(
            [
                "| Champion | Games | W | L | Winrate | KDA | CS/min | Gold/min | Damage/min | Vision/min | Primary role |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for champion in champions:
            assert isinstance(champion, dict)
            lines.append(
                f"| {champion.get('champion', 'N/A')} | {champion.get('games', 0)} | {champion.get('wins', 0)} | {champion.get('losses', 0)} | "
                f"{_metric(champion.get('winrate'), 1, ' %')} | {_metric(champion.get('kda'))} | {_metric(champion.get('cs_per_min'))} | "
                f"{_metric(champion.get('gold_per_min'), 1)} | {_metric(champion.get('damage_per_min'), 1)} | {_metric(champion.get('vision_per_min'))} | {champion.get('primary_role') or 'N/A'} |"
            )
    else:
        lines.append("Aucune partie dans cette sélection.")
    lines.extend(["", "## Match Details", ""])
    if not matches:
        lines.append("Aucune partie dans cette sélection.")
    for index, match in enumerate(matches, start=1):
        assert isinstance(match, dict)
        result = "WIN" if match.get("win") is True else "LOSS" if match.get("win") is False else "N/A"
        lines.extend(
            [
                f"### Match {index}",
                "",
                f"- Match ID: {match.get('match_id') or 'N/A'}",
                f"- Date: {match.get('date') or 'N/A'}",
                f"- Champion / role / side: {match.get('champion') or 'N/A'} / {match.get('role') or 'N/A'} / {match.get('side') or 'N/A'}",
                f"- Result: {result}",
                f"- Queue / patch / duration: {match.get('queue_name') or 'N/A'} / {match.get('patch') or 'N/A'} / {match.get('duration_formatted') or 'N/A'}",
                f"- K/D/A and KDA: {match.get('kills')}/{match.get('deaths')}/{match.get('assists')} — {_metric(match.get('kda'))}",
                f"- CS / CS per min: {match.get('cs') if match.get('cs') is not None else 'N/A'} / {_metric(match.get('cs_per_min'))}",
                f"- Gold / Gold per min: {match.get('gold') if match.get('gold') is not None else 'N/A'} / {_metric(match.get('gold_per_min'), 1)}",
                f"- Damage / Damage per min: {match.get('damage_to_champions') if match.get('damage_to_champions') is not None else 'N/A'} / {_metric(match.get('damage_per_min'), 1)}",
                f"- Vision / Vision per min: {match.get('vision_score') if match.get('vision_score') is not None else 'N/A'} / {_metric(match.get('vision_per_min'))}",
                f"- Items: {', '.join(_markdown_item(item) for item in match.get('items', [])) or 'None'}",
                f"- Trinket: {_markdown_item(match.get('trinket')) if match.get('trinket') else 'None'}",
                f"- Timeline available: {'yes' if match.get('timeline_available') else 'no'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _markdown_item(item: object) -> str:
    if isinstance(item, dict):
        return f"{item.get('name', 'Item')} ({item.get('id', 'N/A')})"
    return str(item)


def export_filename(
    riot_id: str,
    game_count: int,
    extension: str,
    generated_at: datetime | None = None,
) -> str:
    """Create a filesystem-safe, stable download name."""

    player_name = riot_id.split("#", 1)[0]
    normalized = unicodedata.normalize("NFKD", player_name).encode("ascii", "ignore").decode("ascii")
    slug = (re.sub(r"[^a-z0-9]+", "_", normalized.lower()).strip("_") or "player")[:64]
    date_value = (generated_at or datetime.now().astimezone()).date().isoformat()
    suffix = extension.lower().lstrip(".")
    if suffix not in {"csv", "json", "md", "zip"}:
        raise ValueError("Unsupported export extension.")
    if isinstance(game_count, bool) or not isinstance(game_count, int) or game_count < 0:
        raise ValueError("Invalid export game count.")
    return f"lol_analytics_{slug}_{game_count}_games_{date_value}.{suffix}"
