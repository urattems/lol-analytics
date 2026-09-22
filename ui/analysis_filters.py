"""Shared Streamlit controls for the Explorer and AI Export selections."""

from __future__ import annotations

import math
from datetime import datetime, time

import polars as pl
import streamlit as st

from analytics.explorer import AnalysisFilters, build_base_window
from core.queues import queue_name
from ui.formatting import context_label
from ui.page_helpers import select_dataset_limit, select_short_game_inclusion


FILTER_SUFFIXES = (
    "dataset",
    "custom_limit",
    "champion",
    "role",
    "queue",
    "patch",
    "side",
    "outcome",
    "min_duration",
    "max_duration",
    "include_short_games",
    "timeline_requirement",
    "first_death",
    "enable_gold_10",
    "gold_10",
    "enable_gold_15",
    "gold_15",
    "opponent_champion",
    "session_game_number",
    "recurring_teammate",
    "first_dragon",
    "trajectory",
    "date_filter",
    "date_range",
)


def reset_filter_state(key_prefix: str) -> None:
    """Clear one page's filter widgets without affecting another page."""

    for suffix in FILTER_SUFFIXES:
        st.session_state.pop(f"{key_prefix}_{suffix}", None)


def render_analysis_filter_controls(
    all_games: pl.DataFrame,
    key_prefix: str,
    heading: str,
    allow_reset: bool = True,
) -> tuple[AnalysisFilters, bool]:
    """Render the complete centralized filter contract used by both consumers."""

    header, reset = st.columns([3, 1])
    header.caption(heading)
    if allow_reset:
        reset.button(
            "Réinitialiser les filtres",
            on_click=reset_filter_state,
            args=(key_prefix,),
            key=f"{key_prefix}_reset",
            width="stretch",
        )
    scope, short = st.columns([1, 2], vertical_alignment="bottom")
    with scope:
        limit, _ = select_dataset_limit(key_prefix)
    with short:
        include_short_games = select_short_game_inclusion(key_prefix)
    base = build_base_window(all_games, limit)

    def values(column: str) -> list[object]:
        return sorted(base[column].drop_nulls().unique().to_list()) if column in base.columns else []

    a, b, c = st.columns(3)
    champion = a.selectbox("Champion", ["Tous", *values("champion")], key=f"{key_prefix}_champion")
    role = b.selectbox("Rôle", ["Tous", *values("role")], format_func=context_label, key=f"{key_prefix}_role")
    queue = c.selectbox(
        "File de jeu",
        [None, *values("queue_id")],
        format_func=lambda value: "Toutes" if value is None else queue_name(int(value)),
        key=f"{key_prefix}_queue",
    )
    more = st.expander("Plus de filtres · patch, côté, résultat et durée", expanded=False)
    a, b, c = more.columns(3)
    patch = a.selectbox("Patch", ["Tous", *values("patch")], key=f"{key_prefix}_patch")
    side = b.selectbox(
        "Côté",
        [None, "blue", "red"],
        format_func=lambda value: "Tous" if value is None else "Bleu" if value == "blue" else "Rouge",
        key=f"{key_prefix}_side",
    )
    outcome = c.selectbox(
        "Résultat",
        [None, True, False],
        format_func=lambda value: "Tous" if value is None else "Victoire" if value else "Défaite",
        key=f"{key_prefix}_outcome",
    )
    maximum_duration = int(base["duration"].max() or 60) if not base.is_empty() else 3_600
    maximum_minutes = max(1, math.ceil(maximum_duration / 60))
    min_minutes = int(
        a.number_input(
            "Durée minimum (min)", min_value=0, value=0, step=1, key=f"{key_prefix}_min_duration"
        )
    )
    max_minutes = int(
        b.number_input(
            "Durée maximum (min)",
            min_value=0,
            value=maximum_minutes,
            step=1,
            key=f"{key_prefix}_max_duration",
        )
    )
    inverted = min_minutes > max_minutes
    with st.expander("Contextes avancés · Timeline, adversaires et sessions", expanded=False):
        date_from_ms = date_to_ms = None
        use_dates = st.checkbox("Période personnalisée", key=f"{key_prefix}_date_filter")
        if use_dates and not base["game_creation"].drop_nulls().is_empty():
            timestamps = base["game_creation"].drop_nulls()
            minimum_date = datetime.fromtimestamp(int(timestamps.min()) / 1000).date()
            maximum_date = datetime.fromtimestamp(int(timestamps.max()) / 1000).date()
            selected_dates = st.date_input(
                "Du / au",
                value=(minimum_date, maximum_date),
                min_value=minimum_date,
                max_value=maximum_date,
                key=f"{key_prefix}_date_range",
            )
            if isinstance(selected_dates, tuple) and len(selected_dates) == 2:
                date_from_ms = int(datetime.combine(selected_dates[0], time.min).timestamp() * 1000)
                date_to_ms = int(datetime.combine(selected_dates[1], time.max).timestamp() * 1000)
        timeline_requirement = st.selectbox(
            "Timeline",
            ["all", "available", "missing"],
            format_func=lambda value: {
                "all": "Toutes", "available": "Disponible", "missing": "Manquante"
            }[value],
            key=f"{key_prefix}_timeline_requirement",
        )
        first_death = st.selectbox(
            "Mort avant 10",
            [None, True, False],
            format_func=lambda value: "Toutes" if value is None else "Oui" if value else "Non",
            key=f"{key_prefix}_first_death",
        )

        def optional_values(column: str) -> list[object]:
            return values(column) if column in base.columns else []

        opponent = st.selectbox(
            "Champion adverse",
            [None, *optional_values("opponent_champion")],
            format_func=lambda value: "Tous" if value is None else str(value),
            key=f"{key_prefix}_opponent_champion",
        )
        session_number = st.selectbox(
            "Position dans la session",
            [None, "1", "2", "3", "4+"],
            format_func=lambda value: "Toutes" if value is None else f"Game {value}",
            key=f"{key_prefix}_session_game_number",
        )
        teammate_values: list[object] = []
        if "recurring_teammates" in base.columns:
            teammate_values = sorted(
            set(base.get_column("recurring_teammates").explode(empty_as_null=True).drop_nulls().to_list())
            )
        recurring_teammate = st.selectbox(
            "Coéquipier récurrent",
            [None, *teammate_values],
            format_func=lambda value: "Tous" if value is None else str(value),
            key=f"{key_prefix}_recurring_teammate",
        )
        first_dragon = st.selectbox(
            "Premier dragon",
            [None, "ours", "theirs", "none", "ambiguous"],
            format_func=lambda value: "Tous" if value is None else str(value).title(),
            key=f"{key_prefix}_first_dragon",
        )
        trajectory = st.selectbox(
            "Trajectoire",
            [None, *optional_values("trajectory")],
            format_func=lambda value: "Toutes" if value is None else str(value),
            key=f"{key_prefix}_trajectory",
        )

        gold_ranges: dict[int, tuple[int, int] | None] = {}
        for minute in (10, 15):
            enabled = st.checkbox(
                f"Filtrer Gold diff @{minute}", key=f"{key_prefix}_enable_gold_{minute}"
            )
            column = f"gold_diff_{minute}"
            series = (
                base.get_column(column).drop_nulls()
                if column in base.columns else pl.Series([], dtype=pl.Int64)
            )
            low = int(series.min()) if len(series) else -500
            high = int(series.max()) if len(series) else 500
            gold_ranges[minute] = (
                st.slider(
                    f"Gold diff @{minute}", low, max(low + 1, high), (low, max(low, high)),
                    key=f"{key_prefix}_gold_{minute}",
                )
                if enabled else None
            )
    return (
        AnalysisFilters(
            base_game_limit=limit,
            champion=None if champion == "Tous" else str(champion),
            role=None if role == "Tous" else str(role),
            queue_id=int(queue) if queue is not None else None,
            patch=None if patch == "Tous" else str(patch),
            side=side,
            win=outcome,
            min_duration=min_minutes * 60 if min_minutes else None,
            max_duration=max_minutes * 60 if max_minutes * 60 < maximum_duration else None,
            include_short_games=include_short_games,
            timeline_requirement=timeline_requirement,
            first_death_before_10=first_death,
            gold_diff_10_min=gold_ranges[10][0] if gold_ranges[10] else None,
            gold_diff_10_max=gold_ranges[10][1] if gold_ranges[10] else None,
            gold_diff_15_min=gold_ranges[15][0] if gold_ranges[15] else None,
            gold_diff_15_max=gold_ranges[15][1] if gold_ranges[15] else None,
            opponent_champion=str(opponent) if opponent is not None else None,
            session_game_number=session_number,
            recurring_teammate=(
                str(recurring_teammate) if recurring_teammate is not None else None
            ),
            first_dragon=first_dragon,
            trajectory=str(trajectory) if trajectory is not None else None,
            date_from_ms=date_from_ms,
            date_to_ms=date_to_ms,
        ),
        inverted,
    )
