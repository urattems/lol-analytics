"""Recurring teammate and squad analytics using only local participant rows."""

from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations

import polars as pl

from analytics.explorer import analysis_summary
from analytics.overview import per_match_kda_expression, per_minute_expression
from analytics.privacy import stable_alias
from analytics.samples import sample_size_label
from core.identities import local_identity_index


RECURRING_TEAMMATE_MIN_GAMES = 3


def _valid_identity(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value.casefold() not in {"none", "null", "unknown", "n/a"}
        and all(char.isascii() and (char.isalnum() or char in "-_") for char in value)
    )


def teammate_occurrences(
    participants: list[dict[str, object]], player_puuid: str
) -> list[dict[str, object]]:
    """Return one row per teammate/game with real local display name when known."""

    if not _valid_identity(player_puuid):
        return []
    by_match: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in participants:
        if row.get("match_id"):
            by_match[str(row["match_id"])].append(row)
    occurrences: list[dict[str, object]] = []
    identities = local_identity_index(participants)
    for match_id, rows in by_match.items():
        own = [row for row in rows if row.get("puuid") == player_puuid]
        if len(own) != 1:
            continue
        side = own[0].get("side")
        if side not in ("blue", "red"):
            continue
        seen: set[str] = set()
        for row in rows:
            teammate_puuid = row.get("puuid")
            if (
                not _valid_identity(teammate_puuid)
                or teammate_puuid == player_puuid
                or row.get("side") != side
                or teammate_puuid in seen
            ):
                continue
            assert isinstance(teammate_puuid, str)
            seen.add(teammate_puuid)
            game_name = row.get("teammate_game_name")
            tag_line = row.get("teammate_tag_line")
            alias = stable_alias(str(teammate_puuid), "teammate")
            display = identities[teammate_puuid].label
            occurrences.append(
                {
                    "match_id": match_id,
                    "teammate_puuid": teammate_puuid,
                    "teammate_alias": alias,
                    "display_name": display,
                    "teammate_champion": row.get("champion"),
                    "game_creation": row.get("game_creation"),
                }
            )
    return occurrences


def recurring_teammate_ids(
    occurrences: list[dict[str, object]], minimum_games: int = RECURRING_TEAMMATE_MIN_GAMES
) -> set[str]:
    unique_games = {
        (str(row["teammate_puuid"]), str(row["match_id"]))
        for row in occurrences
        if _valid_identity(row.get("teammate_puuid")) and row.get("match_id")
    }
    counts = Counter(identifier for identifier, _ in unique_games)
    return {identifier for identifier, count in counts.items() if count >= minimum_games}


def _metric_row(games: pl.DataFrame) -> dict[str, object]:
    summary = analysis_summary(games)
    row: dict[str, object] = {
        "games": games.height,
        "wins": int(summary["wins"]),
        "winrate": float(summary["winrate"]),
        "kda": summary["kda"],
        "damage_per_min": summary["damage_per_minute"],
        "sample_size": sample_size_label(games.height),
    }
    for column in ("gold_diff_15", "team_gold_diff_15", "cs_diff_15"):
        row[column] = (
            games.select(pl.col(column).drop_nulls().mean()).item()
            if column in games.columns else None
        )
    return row


def teammate_summary(
    games: pl.DataFrame,
    participants: list[dict[str, object]],
    player_puuid: str,
) -> list[dict[str, object]]:
    occurrences = teammate_occurrences(participants, player_puuid)
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    selected_ids = set(games["match_id"].to_list())
    for row in occurrences:
        if row["match_id"] in selected_ids:
            grouped[str(row["teammate_puuid"])].append(row)
    rows: list[dict[str, object]] = []
    metrics = _grouped_metrics(games, [[str(row["match_id"]) for row in values] for values in grouped.values()])
    for index, (identifier, values) in enumerate(grouped.items()):
        dates = [int(row["game_creation"]) for row in values if row.get("game_creation") is not None]
        rows.append(
            {
                "teammate_puuid": identifier,
                "teammate_alias": values[0]["teammate_alias"],
                "display_name": values[0]["display_name"],
                "first_game": min(dates) if dates else None,
                "last_game": max(dates) if dates else None,
                **metrics[index],
            }
        )
    return sorted(rows, key=lambda row: (-int(row["games"]), str(row["display_name"])))


def enrich_teammate_context(
    games: pl.DataFrame,
    participants: list[dict[str, object]],
    player_puuid: str,
) -> pl.DataFrame:
    replaceable = [
        column for column in ("recurring_teammates", "with_recurring_teammate")
        if column in games.columns
    ]
    base = games.drop(replaceable) if replaceable else games
    occurrences = teammate_occurrences(participants, player_puuid)
    recurring = recurring_teammate_ids(occurrences)
    aliases_by_match: dict[str, list[str]] = defaultdict(list)
    for row in occurrences:
        if str(row["teammate_puuid"]) in recurring:
            aliases_by_match[str(row["match_id"])].append(str(row["teammate_alias"]))
    annotations = [
        {
            "match_id": str(match_id),
            "recurring_teammates": sorted(aliases_by_match.get(str(match_id), [])),
            "with_recurring_teammate": bool(aliases_by_match.get(str(match_id))),
        }
        for match_id in base["match_id"].to_list()
    ]
    return base.join(pl.from_dicts(annotations, strict=False), on="match_id", how="left")


def solo_vs_known(games: pl.DataFrame) -> list[dict[str, object]]:
    if games.is_empty() or "with_recurring_teammate" not in games.columns:
        return []
    return [
        {
            "label": "Avec ≥1 coéquipier récurrent" if value else "Solo / aucun coéquipier récurrent",
            **_metric_row(games.filter(pl.col("with_recurring_teammate") == value)),
        }
        for value in (False, True)
    ]


def squad_summary(
    games: pl.DataFrame,
    participants: list[dict[str, object]],
    player_puuid: str,
    minimum_games: int = RECURRING_TEAMMATE_MIN_GAMES,
) -> list[dict[str, object]]:
    occurrences = teammate_occurrences(participants, player_puuid)
    by_match: dict[str, list[dict[str, object]]] = defaultdict(list)
    selected_ids = set(games["match_id"].to_list())
    for row in occurrences:
        if row["match_id"] in selected_ids:
            by_match[str(row["match_id"])].append(row)
    groups: dict[tuple[str, ...], list[str]] = defaultdict(list)
    display: dict[str, str] = {}
    aliases: dict[str, str] = {}
    for match_id, values in by_match.items():
        identifiers = sorted(str(row["teammate_puuid"]) for row in values)
        for row in values:
            display[str(row["teammate_puuid"])] = str(row["display_name"])
            aliases[str(row["teammate_puuid"])] = str(row["teammate_alias"])
        for size in (2, 3, 4):
            for group in combinations(identifiers, size):
                groups[group].append(match_id)
    rows: list[dict[str, object]] = []
    for group, match_ids in groups.items():
        if len(match_ids) < minimum_games:
            continue
        selected = games.filter(pl.col("match_id").is_in(match_ids))
        rows.append(
            {
                "member_puuids": list(group),
                "member_aliases": [aliases[item] for item in group],
                "display_names": [display[item] for item in group],
                "size": len(group),
                **_metric_row(selected),
            }
        )
    return sorted(rows, key=lambda row: (-int(row["games"]), -int(row["size"])))


def champion_pairings(
    games: pl.DataFrame,
    participants: list[dict[str, object]],
    player_puuid: str,
) -> list[dict[str, object]]:
    own_champions = {
        str(row["match_id"]): row.get("champion") for row in games.to_dicts()
    }
    counts: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)
    for row in teammate_occurrences(participants, player_puuid):
        if str(row["match_id"]) not in own_champions:
            continue
        key = (
            str(row["teammate_alias"]), str(row["display_name"]),
            str(own_champions.get(str(row["match_id"])) or "N/A"),
            str(row.get("teammate_champion") or "N/A"),
        )
        counts[key].append(str(row["match_id"]))
    rows = []
    metrics = _grouped_metrics(games, list(counts.values()))
    for index, ((alias, display, own_champion, teammate_champion), ids) in enumerate(counts.items()):
        rows.append({
            "teammate_alias": alias,
            "display_name": display,
            "player_champion": own_champion,
            "teammate_champion": teammate_champion,
            **metrics[index],
        })
    return sorted(rows, key=lambda row: (-int(row["games"]), str(row["display_name"])))


def _grouped_metrics(games: pl.DataFrame, groups: list[list[str]]) -> dict[int, dict[str, object]]:
    """One membership join/aggregation, instead of scanning history per stranger."""
    if not groups:
        return {}
    membership = pl.DataFrame({
        "_group": [index for index, ids in enumerate(groups) for _ in set(ids)],
        "match_id": [match_id for ids in groups for match_id in set(ids)],
    })
    prepared = games.with_columns(
        per_match_kda_expression().alias("_qa_kda"),
        per_minute_expression("damage_dealt").alias("_qa_damage_per_min"),
    )
    joined = membership.join(prepared, on="match_id", how="inner")
    aggregate = joined.group_by("_group").agg(
        pl.len().alias("games"), pl.col("win").fill_null(0).sum().alias("wins"),
        pl.col("_qa_kda").mean().alias("kda"),
        pl.col("_qa_damage_per_min").mean().alias("damage_per_min"),
        *[(pl.col(column).mean() if column in games.columns else pl.lit(None, dtype=pl.Float64)).alias(column)
          for column in ("gold_diff_15", "team_gold_diff_15", "cs_diff_15")],
    )
    result = {}
    for row in aggregate.to_dicts():
        index = row.pop("_group")
        row["winrate"] = row["wins"] * 100.0 / row["games"]
        row["sample_size"] = sample_size_label(row["games"])
        result[index] = row
    return result
