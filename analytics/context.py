"""One local context dataset shared by pages, filters and custom export."""

from __future__ import annotations

import polars as pl

from analytics.insights import join_timeline
from analytics.matchups import identify_matchups
from analytics.sessions import SESSION_GAP_MINUTES, assign_sessions
from analytics.teammates import enrich_teammate_context
from analytics.timeline import load_timeline_analytics, timeline_frame
from core.db import Database


def build_context_dataset(
    games: pl.DataFrame,
    database: Database,
    player_puuid: str,
    session_gap_minutes: int = SESSION_GAP_MINUTES,
) -> pl.DataFrame:
    """Enrich every local match by left joins; context failure must never drop rows."""

    if games.is_empty():
        return games.with_columns(
            pl.lit(False).alias("timeline_available"),
            pl.lit(False).alias("matchup_available"),
            pl.lit(None, dtype=pl.String).alias("opponent_puuid"),
            pl.lit(None, dtype=pl.String).alias("opponent_champion"),
            pl.lit([], dtype=pl.List(pl.String)).alias("recurring_teammates"),
            pl.lit(False).alias("with_recurring_teammate"),
        )
    timeline = timeline_frame(load_timeline_analytics(database, player_puuid))
    enriched = join_timeline(games, timeline)
    participants = database.participants_for_player_matches(player_puuid)
    enriched = identify_matchups(enriched, participants, player_puuid)
    enriched = assign_sessions(enriched, session_gap_minutes)
    enriched = enrich_teammate_context(enriched, participants, player_puuid)
    if enriched.height != games.height:
        raise ValueError("Context enrichment changed the local match count.")
    return enriched
