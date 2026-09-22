"""Local-only parametric match Explorer page."""

from __future__ import annotations

import plotly.graph_objects as go
import polars as pl
import streamlit as st

from analytics.context import build_context_dataset
from analytics.explorer import analysis_summary, build_analysis_dataset
from analytics.overview import champion_summary, per_match_kda_expression, per_minute_expression
from ui.profiles import get_active_settings, get_active_player
from html import escape
from core.db import Database
from ui.analysis_filters import render_analysis_filter_controls
from ui.components import render_metric_grid, page_header, scope_bar
from ui.charts import chart_style, match_tick_step
from ui.formatting import decimal_label, duration_label, kda_line, match_date_label, context_label, selection_filter_label
from ui.page_helpers import cached_player_matches, database_revision, cached_context_dataset
from ui.theme import GREEN, RED, TEAL


def _timeline_figure(games: pl.DataFrame) -> go.Figure:
    chronological = (
        games.sort(["game_creation", "match_id"])
        .with_row_index("match_number", offset=1)
        .with_columns(per_match_kda_expression().alias("match_kda"))
    )
    rows = chronological.to_dicts()
    colors = [GREEN if row.get("win") == 1 else RED for row in rows]
    figure = go.Figure(
        go.Scatter(
            x=[row["match_number"] for row in rows],
            y=[row["match_kda"] for row in rows],
            mode="lines+markers",
            line={"color": TEAL, "width": 2},
            marker={"color": colors, "size": 9},
            customdata=[
                [match_date_label(row.get("game_creation"), True), row.get("champion"), "Victoire" if row.get("win") == 1 else "Défaite"]
                for row in rows
            ],
            hovertemplate="%{customdata[0]}<br><b>%{customdata[1]} · %{customdata[2]}</b><br>KDA : %{y:.2f}<extra></extra>",
        )
    )
    figure.update_layout(
        height=340,
        margin={"l": 15, "r": 15, "t": 15, "b": 15},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(8,19,32,.55)",
        font={"color": "#b8c7d5"},
        xaxis={
            "title": "Matchs · ancien → récent",
            "tickmode": "array",
            "tickvals": list(range(1, len(rows) + 1)),
            "range": [0.5, len(rows) + 0.5],
            "gridcolor": "rgba(143,163,184,.10)",
        },
        yaxis={"title": "KDA", "rangemode": "tozero", "gridcolor": "rgba(143,163,184,.10)"},
        showlegend=False,
    )
    figure.update_xaxes(tickmode="linear", dtick=match_tick_step(len(rows)))
    return chart_style(figure, 290)


def _distribution_figure(games: pl.DataFrame) -> go.Figure:
    values = games.with_columns(per_match_kda_expression().alias("match_kda"))["match_kda"].drop_nulls()
    figure = go.Figure(go.Histogram(x=values.to_list(), marker_color=TEAL, nbinsx=min(12, max(3, games.height))))
    figure.update_layout(
        height=320,
        margin={"l": 15, "r": 15, "t": 15, "b": 15},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(8,19,32,.55)",
        font={"color": "#b8c7d5"},
        xaxis_title="KDA par match",
        yaxis_title="Parties",
        showlegend=False,
    )
    return chart_style(figure, 290)


def _champion_figure(games: pl.DataFrame) -> go.Figure:
    summary = champion_summary(games)
    figure = go.Figure()
    figure.add_bar(x=summary["champion"], y=summary["wins"], name="Victoires", marker_color=GREEN)
    figure.add_bar(x=summary["champion"], y=summary["losses"], name="Défaites", marker_color=RED)
    figure.update_layout(
        barmode="stack",
        height=320,
        margin={"l": 15, "r": 15, "t": 15, "b": 15},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(8,19,32,.55)",
        font={"color": "#b8c7d5"},
        yaxis_title="Parties",
        legend={"orientation": "h", "y": 1.1},
    )
    return chart_style(figure, 290)


def _match_table(games: pl.DataFrame) -> None:
    enriched = games.with_columns(
        per_match_kda_expression().alias("match_kda"),
        per_minute_expression("cs_total").alias("match_cs_min"),
    )
    rows = []
    for row in enriched.to_dicts():
        rows.append(
            {
                "Date": match_date_label(row.get("game_creation")),
                "Champion": row.get("champion") or "N/A",
                "Rôle": context_label(row.get("role") or "N/A"),
                "Résultat": "WIN" if row.get("win") == 1 else "LOSS" if row.get("win") == 0 else "N/A",
                "K/D/A": kda_line(row.get("kills"), row.get("deaths"), row.get("assists")),
                "KDA": decimal_label(row.get("match_kda"), 2),
                "CS/min": decimal_label(row.get("match_cs_min"), 2),
                "Gold": row.get("gold_earned") if row.get("gold_earned") is not None else "N/A",
                "Damage": row.get("damage_dealt") if row.get("damage_dealt") is not None else "N/A",
                "Durée": duration_label(row.get("duration")),
                "Patch": row.get("patch") or "N/A",
                "Side": str(row.get("side") or "N/A").title(),
            }
        )
    st.dataframe(rows, width="stretch", hide_index=True)


def show_explorer() -> None:
    """Render the local-only Explorer with reusable filters."""

    settings = get_active_settings()
    database = Database(settings.database_path)
    player = get_active_player(database, settings)
    page_header("Posez votre question aux données.", "Composez une sélection, voyez ses résultats, puis exportez exactement ces parties.", "EXPLORER")
    if not player:
        st.info("Aucune partie synchronisée.")
        return
    all_games = cached_player_matches(
        str(settings.database_path),
        str(player["puuid"]),
        None,
        database_revision(settings.database_path),
    )
    enriched_games = cached_context_dataset(str(settings.database_path), str(player["puuid"]), database_revision(settings.database_path))
    filters, inverted = render_analysis_filter_controls(
        enriched_games, "explorer", "Filtres Explorer"
    )
    if inverted:
        st.warning("La durée minimum dépassait la durée maximum : les deux bornes ont été inversées.")
    selection = build_analysis_dataset(enriched_games, filters)
    st.session_state["explorer_filters"] = filters.model_dump()

    games = selection.filtered

    active = filters.active_filters()
    active_labels = [selection_filter_label(key, value) for key, value in active.items()]
    active_text = ", ".join(active_labels) or "Aucun filtre secondaire"
    scope_bar(selection.base.height, games.height, "Cette sélection est disponible à l’identique dans AI Export")
    if active_labels:
        st.markdown(
            '<div class="la-chips">' + ''.join(f'<span>{escape(label)}</span>' for label in active_labels) + '</div>',
            unsafe_allow_html=True,
        )
    else:
        st.caption(f"Filtres actifs : {active_text}")
    if games.is_empty():
        st.info("Aucune partie ne correspond à ces filtres.")
        return

    summary = analysis_summary(games)
    render_metric_grid(
        [
            ("Games", str(summary["games"])),
            ("Winrate", decimal_label(summary["winrate"], 1, " %")),
            ("KDA", decimal_label(summary["kda"], 2)),
            ("CS/min", decimal_label(summary["cs_per_minute"], 2)),
            ("Gold/min", decimal_label(summary["gold_per_minute"], 0)),
            ("Damage/min", decimal_label(summary["damage_per_minute"], 0)),
            ("Vision/min", decimal_label(summary["vision_per_minute"], 2)),
        ],
        columns=4,
    )
    st.subheader("Performance dans le temps")
    st.plotly_chart(_timeline_figure(games), width="stretch", config={"displayModeBar": False})
    left, right = st.columns(2)
    with left:
        st.subheader("Distribution KDA")
        st.plotly_chart(_distribution_figure(games), width="stretch", config={"displayModeBar": False})
    with right:
        st.subheader("Résultats par champion")
        st.plotly_chart(_champion_figure(games), width="stretch", config={"displayModeBar": False})
    st.subheader("Matchs filtrés")
    _match_table(games)


if __name__ == '__main__':
    # A cold deep link can discover pages/ before the native router first runs.
    # Register the shared shell; regular imports only expose the page callable.
    from app import main
    main()
