"""Shared Streamlit dataset controls without business-logic duplication."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import streamlit as st

from analytics.explorer import AnalysisFilters, build_analysis_dataset
from analytics.constants import DEFAULT_INCLUDE_SHORT_GAMES
from analytics.context import build_context_dataset
from analytics.overview import get_player_matches
from core.db import Database


DATASET_OPTIONS: dict[str, int | str | None] = {
    "20 dernières": 20,
    "50 dernières": 50,
    "100 dernières": 100,
    "200 dernières": 200,
    "500 dernières": 500,
    "Tout l'historique": None,
    "Personnalisé": "custom",
}


@st.cache_data(show_spinner=False, max_entries=12)
def cached_context_dataset(database_path: str, puuid: str, database_revision: int) -> pl.DataFrame:
    """Share per-profile context across pages, invalidated on local DB changes."""
    games = cached_player_matches(database_path, puuid, None, database_revision)
    return build_context_dataset(games, Database(database_path), puuid)


@st.cache_data(show_spinner=False)
def cached_player_matches(
    database_path: str, puuid: str, limit: int | None, database_revision: int
) -> pl.DataFrame:
    """Cache immutable rows and use the DB revision for external invalidation."""

    del database_revision
    return get_player_matches(Database(database_path), puuid, limit)


def database_revision(database_path: Path) -> int:
    """Return a cheap cache version for a mutable local database."""

    parts = []
    # A committed WAL transaction need not change the main database file yet.
    for path in (database_path, Path(str(database_path) + "-wal")):
        try:
            stat = path.stat()
            parts.append((stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            parts.append((0, 0))
    return hash(tuple(parts))


def select_dataset_limit(key_prefix: str) -> tuple[int | None, str]:
    """Render the standard V1 dataset selector with unique widget keys."""

    selection = st.selectbox(
        "Fenêtre d’analyse",
        list(DATASET_OPTIONS),
        index=0,
        key=f"{key_prefix}_dataset",
    )
    value = DATASET_OPTIONS[selection]
    if value == "custom":
        custom = int(
            st.number_input(
                "Nombre de parties",
                min_value=1,
                value=20,
                step=1,
                key=f"{key_prefix}_custom_limit",
            )
        )
        return custom, str(custom)
    if value is None:
        return None, "Tout"
    return int(value), str(value)


def select_short_game_inclusion(key_prefix: str) -> bool:
    """Short matches are an explicit analytical opt-in; never delete them."""

    return st.checkbox(
        "Inclure les parties de moins de 5 minutes",
        value=DEFAULT_INCLUDE_SHORT_GAMES,
        key=f"{key_prefix}_include_short_games",
    )


def load_selected_dataset(
    database: Database,
    database_path: Path,
    puuid: str,
    key_prefix: str,
) -> tuple[pl.DataFrame, int | None]:
    """Select, report, and load a newest-first player dataset."""

    left, right = st.columns([1, 1.5], vertical_alignment="bottom")
    with left:
        limit, requested_label = select_dataset_limit(key_prefix)
    with right:
        include_short_games = select_short_game_inclusion(key_prefix)
    available = database.count_player_matches(puuid)
    games = cached_player_matches(
        str(database_path), puuid, limit, database_revision(database_path)
    )
    games = build_analysis_dataset(
        games,
        AnalysisFilters(
            base_game_limit=None, include_short_games=include_short_games
        ),
    ).filtered
    analyzed = games.height
    from ui.components import scope_bar
    scope_bar(min(available, limit or available), analyzed, f"{available} parties locales · sélection propre à cette page")
    return games, limit
