from __future__ import annotations

import polars as pl

from analytics.context import build_context_dataset
from analytics.overview import PLAYER_MATCH_SCHEMA, get_player_matches
from core.db import Database
from core.models import parse_riot_match


def test_empty_context_dataset_has_safe_filter_columns(database: Database) -> None:
    empty = pl.DataFrame(schema=PLAYER_MATCH_SCHEMA)
    context = build_context_dataset(empty, database, "missing-player")
    assert context.is_empty()
    assert {"timeline_available", "matchup_available", "opponent_champion", "recurring_teammates"}.issubset(context.columns)


def test_match_without_timeline_is_kept_and_marked_missing(
    database: Database, riot_match_payload: dict[str, object]
) -> None:
    database.insert_match(parse_riot_match(riot_match_payload))
    games = get_player_matches(database, "player-puuid", None)
    context = build_context_dataset(games, database, "player-puuid")
    rebuilt = build_context_dataset(context, database, "player-puuid")
    assert context.height == 1
    assert rebuilt.height == 1
    assert len(rebuilt.columns) == len(set(rebuilt.columns))
    assert context["timeline_available"].to_list() == [False]
    assert context["matchup_available"].to_list() == [False]  # two same-role enemies: ambiguous
