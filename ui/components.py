"""Reusable visual components shared across LoL Analytics."""

from __future__ import annotations

from html import escape
from typing import Any

import plotly.graph_objects as go
import polars as pl
import streamlit as st

from analytics.overview import champion_summary, get_overview_summary, recent_matches, rolling_winrate
from core.static_data import DataDragonService
from ui.formatting import context_label, decimal_label, duration_label, kda_line, match_date_label, signed_label
from ui.charts import chart_style, match_tick_step
from ui.theme import GOLD, GREEN, MUTED, RED, TEAL


def page_header(title: str, description: str, eyebrow: str = "ANALYSE LOCALE") -> None:
    st.markdown(
        f'<header class="la-page-header"><div class="la-eyebrow">{escape(eyebrow)}</div>'
        f'<h1>{escape(title)}</h1><p>{escape(description)}</p></header>', unsafe_allow_html=True,
    )


def scope_bar(base: int, matched: int, detail: str = "") -> None:
    st.markdown(
        f'<div class="la-scope"><span><b>{matched}</b> parties analysées'
        f' <span class="la-muted">sur {base} dans la fenêtre</span></span>'
        f'<small>{escape(detail)}</small></div>', unsafe_allow_html=True,
    )


def empty_state(title: str, detail: str) -> None:
    st.markdown(
        f'<div class="la-empty"><span class="la-eyebrow">À EXPLORER</span>'
        f'<h3>{escape(title)}</h3><p>{escape(detail)}</p></div>', unsafe_allow_html=True,
    )


def stat_strip(cards: list[tuple[str, str]]) -> None:
    cells = ''.join(
        f'<div><small>{escape(label)}</small><strong>{escape(value)}</strong></div>'
        for label, value in cards
    )
    st.markdown(f'<div class="la-stat-strip">{cells}</div>', unsafe_allow_html=True)


def discovery_card(observation: dict[str, object]) -> None:
    high, low = observation["high"], observation["low"]
    context = str(observation["context"])
    assert isinstance(high, dict) and isinstance(low, dict)
    st.markdown(
        '<article class="la-discovery">'
        f'<div class="la-eyebrow">{escape(context_label(context))}</div>'
        f'<div class="la-discovery-gap">{decimal_label(observation["winrate_gap"], 1)}<small> pts de WR</small></div>'
        f'<div class="la-compare-line"><span>{escape(context_label(high["label"], context))}</span>'
        f'<b>{decimal_label(high["winrate"], 1, " %")}</b></div>'
        f'<div class="la-compare-line la-muted"><span>{escape(context_label(low["label"], context))}</span>'
        f'<b>{decimal_label(low["winrate"], 1, " %")}</b></div>'
        f'<footer>{high["games"]} et {low["games"]} parties comparées · observation descriptive</footer></article>',
        unsafe_allow_html=True,
    )


def recent_form_strip(games: pl.DataFrame, count: int = 10) -> None:
    selected = recent_matches(games, count).sort(["game_creation", "match_id"])
    marks = ''.join(
        f'<span class="la-form-{ "win" if r["win"] == 1 else "loss"}" '
        f'title="{escape(str(r["champion"]))} · {match_date_label(r["game_creation"])}">'
        f'{"V" if r["win"] == 1 else "D"}</span>' for r in selected.to_dicts()
    )
    st.markdown(f'<div class="la-form"><small>{selected.height} dernières · ancien → récent</small><div>{marks}</div></div>', unsafe_allow_html=True)


def match_history_cards(games: pl.DataFrame, static_data: DataDragonService, limit: int = 6) -> None:
    """Readable match rows with native in-session navigation to the same game."""
    for row in recent_matches(games, limit).to_dicts():
        asset = static_data.champion(str(row["champion"]))
        result = "Victoire" if row["win"] == 1 else "Défaite"
        tone = "win" if row["win"] == 1 else "loss"
        portrait = f'<img loading="lazy" src="{escape(asset.image_url)}" alt="">' if asset.image_url else ''
        kda = kda_line(row.get("kills"), row.get("deaths"), row.get("assists"))
        context = signed_label(row.get("gold_diff_15")) + ' @15' if row.get("gold_diff_15") is not None else 'Timeline non disponible' if not row.get('timeline_available') else 'Checkpoint @15 absent'
        main, action = st.columns([9, 1], vertical_alignment="center")
        main.markdown(
            f'<article class="la-match-row la-result-{tone}">{portrait}'
            f'<div class="la-match-identity"><b>{escape(asset.display_name)}</b><small>{match_date_label(row.get("game_creation"))} · {duration_label(row.get("duration"))}</small></div>'
            f'<div><b class="la-{tone}">{result}</b><small>{escape(str(row.get("queue_name") or ""))}</small></div>'
            f'<div><b>{escape(kda)}</b><small>K / D / A</small></div>'
            f'<div><b>{escape(context)}</b><small>Patch {escape(str(row.get("patch") or "N/A"))}</small></div></article>',
            unsafe_allow_html=True,
        )
        if action.button("Revoir", key=f'review_{row["match_id"]}', help="Ouvrir la Timeline de cette partie", disabled=not row.get("timeline_available"), width="stretch"):
            st.session_state["timeline_requested_match"] = row["match_id"]
            from pages.timeline import show_timeline
            st.switch_page(st.Page(show_timeline, title="Timeline", url_path="timeline"))


def section_header(title: str, copy: str | None = None) -> None:
    """Render a consistent section heading and optional muted explanation."""

    st.subheader(title)
    if copy:
        st.markdown(f'<div class="la-section-copy">{escape(copy)}</div>', unsafe_allow_html=True)


def metric_card(label: str, value: str, detail: str | None = None) -> None:
    """Render one compact metric card using the centralized visual language."""

    st.markdown(
        f'<div class="la-insight-card"><div class="la-eyebrow">{escape(label)}</div>'
        f'<div class="la-insight-value">{escape(value)}</div>'
        f'<div class="la-meta">{escape(detail or "")}</div></div>',
        unsafe_allow_html=True,
    )


def insight_card(
    label: str, value: str, detail: str, accent: str = "cyan"
) -> None:
    """Render one reusable highlighted observation card."""

    st.markdown(
        f'<div class="la-insight-card la-accent-{escape(accent)}">'
        f'<div class="la-eyebrow">{escape(label)}</div>'
        f'<div class="la-insight-value">{escape(value)}</div>'
        f'<div class="la-meta">{escape(detail)}</div></div>',
        unsafe_allow_html=True,
    )


def comparison_card(label: str, left: str, right: str, detail: str) -> None:
    insight_card(label, f"{left} ↔ {right}", detail, "gold")


def sample_badge(n: int, label: str) -> str:
    """Return accessible text for an observation's volume."""

    return f"N = {n} · {label}"


def champion_card(name: str, games: int, winrate: object, kda: object) -> None:
    insight_card(
        name.upper(), f"{decimal_label(winrate, 1, ' %')} WR",
        f"{games} games · {decimal_label(kda, 2)} KDA",
    )


def render_app_header(riot_id: str, database_status: str, last_sync: str, total_matches: int) -> None:
    """Render application identity and non-sensitive local status."""

    st.markdown(
        f"""
        <div class="la-shellbar">
            <div><b>LOL ANALYTICS</b><span>PERSONAL LAB</span></div>
            <div class="la-meta">{escape(riot_id)} &nbsp;•&nbsp; {escape(database_status)} &nbsp;•&nbsp; {total_matches} matchs</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_kpis(summary: dict[str, float | int | None]) -> None:
    """Render the five Overview KPI cards from analytical results."""

    cards = (
        ("Games", str(summary["games"])),
        ("Winrate", decimal_label(summary["winrate"], 1, " %")),
        ("KDA", decimal_label(summary["kda"], 2)),
        ("CS/min", decimal_label(summary["cs_per_minute"], 2)),
        ("Damage/min", decimal_label(summary["damage_per_minute"], 0)),
    )
    render_metric_grid(list(cards), columns=5)


def render_metric_grid(cards: list[tuple[str, str]], columns: int = 5) -> None:
    """Render any number of consistently styled metric cards in rows."""

    labels = {"Games": "Parties", "Matches": "Parties", "Damage/min": "DPM", "Damage": "Dégâts", "Wins": "Victoires", "Losses": "Défaites"}
    cells = ''.join(
        f'<div class="la-stat"><small>{escape(labels.get(label, label))}</small><strong>{escape(value)}</strong></div>'
        for label, value in cards
    )
    st.markdown(f'<div class="la-stats" style="--stat-cols:{columns}">{cells}</div>', unsafe_allow_html=True)


def render_champion_pool(games: pl.DataFrame, static_data: DataDragonService) -> None:
    """Render top champion cards and a compact full aggregation table."""

    champions = champion_summary(games)
    st.subheader("Champions les plus joués")
    st.markdown(
        '<div class="la-section-copy">Les performances observées dans la sélection active.</div>',
        unsafe_allow_html=True,
    )
    top_rows = champions.head(5).to_dicts()
    for column, row in zip(st.columns(max(1, len(top_rows))), top_rows, strict=True):
        with column:
            champion = static_data.champion(str(row["champion"]))
            portrait = (
                f'<img class="la-champion-portrait" src="{escape(champion.image_url)}" alt="">'
                if champion.image_url
                else '<div class="la-champion-portrait la-portrait-fallback"></div>'
            )
            st.markdown(
                f"""
                <div class="la-champion-card">
                    {portrait}
                    <div class="la-champion-name">{escape(champion.display_name.upper())}</div>
                    <div class="la-champion-games">{row['games']} games</div>
                    <div class="la-champion-stats">
                        {decimal_label(row['winrate'], 1, ' %')} WR<br>
                        {decimal_label(row['kda'], 2)} KDA<br>
                        {decimal_label(row['damage_per_minute'], 0)} DPM
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    st.write("")
    display_rows = []
    for row in champions.to_dicts():
        display_rows.append(
            {
                "Champion": row["champion"],
                "Games": row["games"],
                "Wins": row["wins"],
                "Losses": row["losses"],
                "Winrate": decimal_label(row["winrate"], 1, " %"),
                "KDA": decimal_label(row["kda"], 2),
                "CS/min": decimal_label(row["cs_per_minute"], 2),
                "Damage/min": decimal_label(row["damage_per_minute"], 0),
            }
        )
    st.dataframe(display_rows, width="stretch", hide_index=True)


def _rolling_figure(rolling: pl.DataFrame, window: int) -> go.Figure:
    from core.runtime import ensure_plot_runtime
    ensure_plot_runtime()
    # to_dicts yields native scalars; don't pass a Series to Plotly validators.
    rows = rolling.to_dicts()
    dates = [match_date_label(row.get("game_creation"), include_time=True) for row in rows]
    results = ["WIN" if row.get("win") == 1 else "LOSS" if row.get("win") == 0 else "N/A" for row in rows]
    custom_data = [
        [
            dates[index],
            row.get("champion") or "N/A",
            results[index],
            kda_line(row.get("kills"), row.get("deaths"), row.get("assists")),
            duration_label(row.get("duration")),
            row.get("rolling_wins"),
            row.get("rolling_losses"),
        ]
        for index, row in enumerate(rows)
    ]
    marker_colors = [GREEN if result == "WIN" else RED if result == "LOSS" else MUTED for result in results]
    figure = go.Figure(
        go.Scatter(
            x=[row["match_number"] for row in rows],
            y=[row["rolling_winrate"] for row in rows],
            mode="lines+markers",
            line={"color": TEAL, "width": 3},
            marker={"color": marker_colors, "size": 8, "line": {"color": "#0b1626", "width": 1}},
            customdata=custom_data,
            hovertemplate=(
                "%{customdata[0]}<br><b>%{customdata[1]} · %{customdata[2]}</b><br>"
                "K/D/A : %{customdata[3]}<br>Durée : %{customdata[4]}<br>"
                "Fenêtre : %{customdata[5]}W / %{customdata[6]}L<br>"
                f"Winrate glissant ({window}) : %{{y:.1f}} %<extra></extra>"
            ),
        )
    )
    figure.update_layout(
        height=370,
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(8,19,32,0.55)",
        font={"color": "#b8c7d5"},
        hoverlabel={"bgcolor": "#101f32", "bordercolor": GOLD, "font_color": "#f3f6fa"},
        xaxis={"title": "Matchs · ancien → récent", "gridcolor": "rgba(143,163,184,.10)", "dtick": 1},
        yaxis={"title": "Winrate glissant", "range": [0, 100], "ticksuffix": " %", "gridcolor": "rgba(143,163,184,.10)"},
        showlegend=False,
    )
    figure.update_xaxes(dtick=match_tick_step(len(rows)))
    return chart_style(figure, 270)


def render_recent_form(games: pl.DataFrame) -> None:
    """Render the chronological rolling-win-rate Plotly figure."""

    rolling, window = rolling_winrate(games)
    st.subheader("Forme récente")
    st.markdown(
        f'<div class="la-section-copy">Winrate glissant — fenêtre de {window} games.</div>',
        unsafe_allow_html=True,
    )
    if rolling.is_empty():
        st.info(f"Au moins {window} games sont nécessaires pour afficher une fenêtre complète.")
    else:
        from ui.errors import chart_boundary
        with chart_boundary("overview_recent_form"):
            st.plotly_chart(_rolling_figure(rolling, window), width="stretch", config={"displayModeBar": False})


def render_recent_matches(games: pl.DataFrame) -> None:
    """Render five compact newest-first match summaries."""

    st.subheader("Dernières parties")
    st.markdown(
        '<div class="la-section-copy">Les matchs les plus récents de la sélection.</div>',
        unsafe_allow_html=True,
    )
    for row in recent_matches(games, 5).to_dicts():
        result = "WIN" if row.get("win") == 1 else "LOSS" if row.get("win") == 0 else "N/A"
        result_class = "la-win" if result == "WIN" else "la-loss" if result == "LOSS" else ""
        with st.container(border=True):
            champion, outcome, performance, farming, context = st.columns([1.5, 0.8, 1.2, 1, 1.5])
            champion.markdown(f'<div class="la-match-title">{escape(str(row.get("champion") or "N/A"))}</div>', unsafe_allow_html=True)
            champion.markdown(f'<div class="la-match-meta">{match_date_label(row.get("game_creation"))}</div>', unsafe_allow_html=True)
            outcome.markdown(f'<span class="{result_class}">{result}</span>', unsafe_allow_html=True)
            performance.markdown(f"**{kda_line(row.get('kills'), row.get('deaths'), row.get('assists'))}**")
            farming.markdown(f"**CS** {row.get('cs_total') if row.get('cs_total') is not None else 'N/A'}")
            context.markdown(f"**{duration_label(row.get('duration'))}** · Patch {row.get('patch') or 'N/A'}")


def render_overview(games: pl.DataFrame, static_data: DataDragonService) -> None:
    """Render the complete Overview from one player-only dataset."""

    st.header("Overview")
    st.markdown(
        '<div class="la-section-copy">Un aperçu rapide de vos performances récentes.</div>',
        unsafe_allow_html=True,
    )
    if games.is_empty():
        st.info("Aucune partie synchronisée. Utilisez le bouton Synchroniser Riot.")
        return

    summary = get_overview_summary(games)
    render_kpis(summary)
    st.markdown(
        f'<div class="la-outcomes"><span>Wins : <b class="la-win">{summary["wins"]}</b></span>'
        f'<span>Losses : <b class="la-loss">{summary["losses"]}</b></span></div>',
        unsafe_allow_html=True,
    )
    short_games = int(summary["short_games"] or 0)
    if short_games:
        noun = "partie" if short_games == 1 else "parties"
        st.info(
            f"{short_games} {noun} de moins de 5 minutes sont présentes dans cette sélection. "
            "Elles sont actuellement incluses dans les statistiques."
        )

    render_champion_pool(games, static_data)
    st.divider()
    render_recent_form(games)
    st.divider()
    render_recent_matches(games)
