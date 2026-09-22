"""Single-match temporal analysis page backed only by persisted timelines."""

from __future__ import annotations

from collections import defaultdict
from html import escape

import plotly.graph_objects as go
import streamlit as st

from analytics.timeline import event_item_id, load_timeline_analytics
from ui.profiles import get_active_settings, get_active_player
from core.db import Database
from core.queues import queue_name
from core.static_data import get_static_data_service
from ui.components import render_metric_grid, page_header, stat_strip, empty_state
from ui.formatting import decimal_label, duration_label, kda_line, match_date_label, signed_label, context_label
from ui.charts import chart_style
from ui.theme import GOLD, GREEN, RED, TEAL
from core.runtime import ensure_plot_runtime
from ui.errors import chart_boundary
from ui.team_advantage import advantage_regions, advantage_label, BLUE, RED as SIDE_RED


def _participant_id(frames: list[dict[str, object]], puuid: str | None) -> int | None:
    if not puuid:
        return None
    ids = {int(row["participant_id"]) for row in frames if row.get("puuid") == puuid}
    return next(iter(ids)) if len(ids) == 1 else None


def _personal_figure(
    frames: list[dict[str, object]], player_id: int, opponent_id: int | None, metric: str
) -> go.Figure:
    ensure_plot_runtime()
    column = {"Gold": "total_gold", "CS": "cs_total", "XP": "xp"}[metric]
    figure = go.Figure()
    for participant_id, name, color in (
        (player_id, "Vous", TEAL),
        (opponent_id, "Adversaire direct", GOLD),
    ):
        if participant_id is None:
            continue
        rows = [row for row in frames if row.get("participant_id") == participant_id]
        figure.add_scatter(
            x=[int(row["timestamp_ms"]) / 60_000 for row in rows],
            y=[row.get(column) for row in rows],
            mode="lines",
            name=name,
            line={"color": color, "width": 3},
            hovertemplate=f"%{{x:.1f}} min<br>{metric} : %{{y:,.0f}}<extra>{name}</extra>",
        )
    figure.update_layout(
        height=390,
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(8,19,32,.55)",
        font={"color": "#b8c7d5"},
        xaxis={"title": "Minute", "gridcolor": "rgba(143,163,184,.10)"},
        yaxis={"title": metric, "gridcolor": "rgba(143,163,184,.10)"},
        legend={"orientation": "h", "y": 1.08},
        hovermode="x unified",
    )
    return chart_style(figure, 320)


def _team_gold_figure(
    frames: list[dict[str, object]], participants: list[dict[str, object]], player_side: str
) -> go.Figure:
    ensure_plot_runtime()
    side_by_id: dict[int, object] = {}
    for row in frames:
        puuid = row.get("puuid")
        participant = next((p for p in participants if p.get("puuid") == puuid), None)
        if participant:
            side_by_id[int(row["participant_id"])] = participant.get("side")
    totals: dict[int, dict[object, int]] = defaultdict(lambda: defaultdict(int))
    observed: dict[int, set[str]] = defaultdict(set)
    expected = {str(p["puuid"]) for p in participants if p.get("side") in {"blue", "red"}}
    for row in frames:
        gold = row.get("total_gold")
        side = side_by_id.get(int(row["participant_id"]))
        if gold is not None and side in {"blue", "red"}:
            totals[int(row["timestamp_ms"])][side] += int(gold)
            observed[int(row["timestamp_ms"])].add(str(row.get("puuid")))
    opponent_side = "red" if player_side == "blue" else "blue"
    # Retain all-null timestamps as gaps; never bridge missing team data.
    timestamps = sorted({int(row["timestamp_ms"]) for row in frames})
    differences = [totals[t][player_side] - totals[t][opponent_side]
                   if observed[t] == expected and player_side in {"blue", "red"} else None
                   for t in timestamps]
    minutes = [timestamp / 60_000 for timestamp in timestamps]
    figure = go.Figure(
        go.Scatter(
            x=minutes,
            y=differences,
            mode="lines",
            connectgaps=False,
            line={"color": "#d9e6ed", "width": 2.5},
            text=[advantage_label(value, player_side) for value in differences],
            hovertemplate="Minute %{x:g}<br>%{text}<extra></extra>",
            showlegend=False,
        )
    )
    for side, region in advantage_regions(minutes, differences, player_side).items():
        color = BLUE if side == "blue" else SIDE_RED
        figure.add_scatter(x=region["x"] or [None], y=region["y"] or [None],
            mode="lines", line={"color": color, "width": 0}, fill="toself",
            fillcolor="rgba(90,169,255,.28)" if side == "blue" else "rgba(255,113,134,.28)",
            name=f"{side.title()} Side devant", connectgaps=False, hoverinfo="skip")
    figure.add_hline(y=0, line_color="rgba(255,255,255,.45)", line_width=1)
    figure.update_layout(
        height=330,
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(8,19,32,.55)",
        font={"color": "#b8c7d5"},
        xaxis={"title": "Minute", "gridcolor": "rgba(143,163,184,.10)"},
        yaxis={"title": "Écart de votre équipe (gold)", "gridcolor": "rgba(143,163,184,.10)"},
        showlegend=True, legend={"orientation": "h", "y": 1.15},
    )
    return chart_style(figure, 320)


def _render_match_hero(row: dict[str, object]) -> None:
    static = get_static_data_service().champion(str(row.get("champion") or ""))
    result = "WIN" if row.get("win") in {1, True} else "LOSS"
    css_class = "la-win" if result == "WIN" else "la-loss"
    background = (
        f"linear-gradient(90deg, rgba(5,13,24,.97), rgba(5,13,24,.50)), url('{escape(static.splash_url)}')"
        if static.splash_url
        else "linear-gradient(120deg,#132842,#0a1728)"
    )
    st.markdown(
        f"""
        <div class="la-match-hero" style="background-image:{background}">
          <div class="la-eyebrow">{escape(queue_name(row.get('queue_id')))} · Patch {escape(str(row.get('patch') or 'N/A'))}</div>
          <div class="la-title">{escape(static.display_name.upper())} <span class="{css_class}">{result}</span></div>
          <div class="la-meta">{escape(match_date_label(row.get('game_creation')))} · {escape(duration_label(row.get('duration')))} · {escape(str(row.get('side') or 'N/A').title())} Side</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _format_delta(value: object) -> str:
    return "N/A" if value is None else f"{int(value):+,}"


def _render_checkpoints(row: dict[str, object]) -> None:
    columns = st.columns(3)
    for column, minute in zip(columns, (10, 15, 20), strict=True):
        point = row.get(f"checkpoint_{minute}")
        with column:
            st.markdown(
                f'<div class="la-insight-card"><div class="la-eyebrow">À {minute} MIN · ADVERSAIRE DIRECT</div>'
                f'<div class="la-insight-value">{signed_label(row.get(f"gold_diff_{minute}"))}</div>'
                f'<div class="la-meta">CS {signed_label(row.get(f"cs_diff_{minute}"), "")} · XP {signed_label(row.get(f"xp_diff_{minute}"), "")}</div></div>',
                unsafe_allow_html=True,
            )
            if isinstance(point, dict):
                actual = int(point["actual_frame_timestamp_ms"]) / 60_000
                st.caption(f"Frame Riot réel : {actual:.1f} min")
            else:
                st.caption("N/A — partie trop courte ou frame absente")


def _render_purchases(row: dict[str, object]) -> None:
    purchases = row.get("purchases")
    if not isinstance(purchases, list) or not purchases:
        st.info("Aucun achat exploitable dans cette Timeline.")
        return
    static_data = get_static_data_service()
    blocks = []
    labels = {"ITEM_PURCHASED": "Achat", "ITEM_SOLD": "Vente", "ITEM_UNDO": "Annulation"}
    for purchase in purchases:
        if not isinstance(purchase, dict):
            continue
        item_id = event_item_id(purchase)
        if item_id is None:
            continue
        item = static_data.item(item_id)
        timestamp = int(purchase.get("timestamp_ms") or 0) // 1000
        minute, second = divmod(timestamp, 60)
        icon = f'<img src="{escape(item.image_url)}" alt="">' if item.image_url else ""
        blocks.append(
            f'<div class="la-purchase">{icon}<div><b>{minute:02d}:{second:02d}</b><span>{labels.get(str(purchase.get("event_type")), "Événement")} — {escape(item.display_name)}</span></div></div>'
        )
    st.markdown(f'<div class="la-purchase-grid">{"".join(blocks)}</div>', unsafe_allow_html=True)


def _render_event_feed(
    events: list[dict[str, object]], player_id: int
) -> None:
    selected = st.multiselect(
        "Marqueurs affichés",
        ["Kills & deaths", "Items", "Objectifs"],
        default=["Kills & deaths", "Items", "Objectifs"],
        key="timeline_event_markers",
    )
    static_data = get_static_data_service()
    rows = []
    for event in events:
        event_type = event.get("event_type")
        category = detail = None
        if event_type == "CHAMPION_KILL":
            if event.get("killer_id") == player_id:
                category, detail = "Kills & deaths", "Kill"
            elif event.get("victim_id") == player_id:
                category, detail = "Kills & deaths", "Mort"
        elif event_type in {"ITEM_PURCHASED", "ITEM_SOLD", "ITEM_UNDO"} and event.get("participant_id") == player_id:
            category = "Items"
            action = {"ITEM_PURCHASED": "Achat", "ITEM_SOLD": "Vente", "ITEM_UNDO": "Annulation"}[str(event_type)]
            item_id = event_item_id(event)
            detail = f"{action} · {static_data.item(item_id).display_name}" if item_id else f"{action} · Objet inconnu"
        elif event_type in {"ELITE_MONSTER_KILL", "BUILDING_KILL"}:
            category = "Objectifs"
            detail = str(
                event.get("monster_subtype") or event.get("monster_type")
                or event.get("tower_type") or event.get("building_type") or "Objectif"
            ).replace("_", " ").title()
        if category not in selected or detail is None:
            continue
        seconds = int(event.get("timestamp_ms") or 0) // 1000
        minute, second = divmod(seconds, 60)
        rows.append({"Temps": f"{minute:02d}:{second:02d}", "Type": category, "Événement": detail})
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True, height=min(420, 38 * len(rows) + 42))
    else:
        st.caption("Aucun marqueur correspondant.")


def show_timeline() -> None:
    """Render persisted match timelines; no Riot call occurs during page display."""

    settings = get_active_settings()
    database = Database(settings.database_path)
    player = get_active_player(database, settings)
    page_header("L’histoire d’une partie.", "Les écarts, les achats et les moments clés, minute par minute.", "TIMELINE")
    if not player:
        st.info("Aucune partie synchronisée.")
        return
    puuid = str(player["puuid"])
    from ui.session_navigation import timeline_session_navigation
    from ui.history_navigation import timeline_history_navigation
    session_match = timeline_history_navigation(database, puuid) or timeline_session_navigation(database, puuid)
    coverage = database.timeline_coverage(puuid)
    st.caption(f"{coverage['available']} timelines disponibles sur {coverage['local']} parties locales")
    rows = load_timeline_analytics(database, puuid)
    if session_match and not any(row["match_id"] == session_match for row in rows):
        details = next((row for row in database.player_matches(puuid) if row["match_id"] == session_match), None)
        if details:
            _render_match_hero(details)
            from ui.journal_navigation import journal_button
            journal_button(puuid, session_match, 'timeline_note')
            from core.ranks import load_rank_contexts
            from ui.ranks import render_rank_panel
            render_rank_panel(load_rank_contexts(database, puuid, [session_match]).get(session_match), settings, puuid, key_prefix='timeline')
        st.info("La Timeline de cette partie est indisponible. Revenez à la session pour l’enrichir, ou passez à la partie suivante.")
        return
    if not rows:
        st.info("Aucune Timeline enrichie. Utilisez ‘Enrichir les timelines’ dans la Bibliothèque locale.")
        return

    champion_options = ["Tous", *sorted({str(row["champion"]) for row in rows})]
    requested = st.session_state.pop("timeline_requested_match", None)
    if requested and any(row["match_id"] == requested for row in rows):
        st.session_state["timeline_champion"] = "Tous"
        st.session_state["timeline_outcome"] = "Tous"
        st.session_state["timeline_selected_match"] = requested
    filter_a, filter_b, filter_c = st.columns([1.2, 1, 2.4])
    champion = filter_a.selectbox("Champion", champion_options, key="timeline_champion")
    outcome = filter_b.selectbox("Résultat", ["Tous", "WIN", "LOSS"], key="timeline_outcome")
    filtered = [
        row for row in rows
        if (champion == "Tous" or row.get("champion") == champion)
        and (outcome == "Tous" or (row.get("win") in {1, True}) == (outcome == "WIN"))
    ]
    if not filtered:
        st.info("Aucune Timeline ne correspond à cette sélection.")
        return
    labels = {
        str(row["match_id"]): f"{row['champion']} · {'WIN' if row.get('win') in {1, True} else 'LOSS'} · {duration_label(row.get('duration'))} · {match_date_label(row.get('game_creation'))}"
        for row in filtered
    }
    if st.session_state.get("timeline_selected_match") not in labels:
        st.session_state.pop("timeline_selected_match", None)
    selected_id = filter_c.selectbox("Partie", list(labels), format_func=labels.get, key="timeline_selected_match")
    row = next(item for item in filtered if item["match_id"] == selected_id)
    match_details = next(item for item in database.player_matches(puuid) if item["match_id"] == selected_id)
    row.update(match_details)
    frames = database.timeline_frames(selected_id)
    participants = database.match_participants(selected_id)
    events = database.timeline_events(selected_id)
    player_id = int(row["player_participant_id"])
    opponent_id = row.get("opponent_participant_id")

    _render_match_hero(row)
    from ui.journal_navigation import journal_button
    journal_button(puuid, selected_id, 'timeline_note')
    from core.ranks import load_rank_contexts
    from ui.ranks import render_rank_panel
    render_rank_panel(load_rank_contexts(database, puuid, [selected_id]).get(selected_id), settings, puuid, key_prefix='timeline')
    stat_strip(
        [
            ("K/D/A", kda_line(row.get("kills"), row.get("deaths"), row.get("assists"))),
            ("CS", decimal_label(row.get("cs_total"), 0)),
            ("Gold", decimal_label(row.get("gold_earned"), 0)),
            ("Dégâts", decimal_label(row.get("damage_dealt"), 0)),
            ("Side", str(row.get("side") or "N/A").title()),
        ],
    )
    _render_checkpoints(row)
    trajectory = row.get("trajectory")
    if trajectory:
        st.caption(f'Trajectoire de l’équipe @10 → @15 → @20 : {context_label(trajectory)}')
    curves, purchases, feed = st.tabs(["Évolution de la partie", "Achats & moments clés", "Journal d’événements"])
    with curves:
        view = st.segmented_control("Courbe", ["Gold personnel", "CS", "XP", "Écart d’équipe"], default="Gold personnel", key="timeline_curve") or "Gold personnel"
        with chart_boundary("timeline_curve"):
            if view == "Écart d’équipe":
                figure = _team_gold_figure(frames, participants, str(row.get("side")))
            else:
                figure = _personal_figure(frames, player_id, int(opponent_id) if opponent_id else None, "Gold" if view == "Gold personnel" else view)
            st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
        if view == "Écart d’équipe":
            side = row.get("side")
            st.caption(f"Vous : {side.title()} Side. Au-dessus de zéro : votre équipe devant. La couleur indique le camp qui mène." if side in ("blue", "red") else "Votre camp est indisponible : avantage N/A.")
        if opponent_id is None:
            st.caption("Adversaire direct indisponible : le rôle adverse n’était pas identifiable sans ambiguïté.")
    with purchases:
        stat_strip([
            ("Premier kill", duration_label(row["first_kill_ms"] / 1000) if row.get("first_kill_ms") is not None else "N/A"),
            ("Première mort", duration_label(row["first_death_ms"] / 1000) if row.get("first_death_ms") is not None else "N/A"),
            ("Kills avant 10", str(row.get("kills_before_10", 0))),
            ("Morts avant 10", str(row.get("deaths_before_10", 0))),
        ])
        st.caption(f'Participation au first blood : {"Oui" if row.get("first_blood_participation") else "Non"}')
        st.subheader("Achats, ventes et annulations")
        _render_purchases(row)
    with feed:
        objectives = row.get("objectives")
        st.caption(f"Objectifs détectés dans la partie : {len(objectives) if isinstance(objectives, list) else 0}")
        _render_event_feed(events, player_id)


if __name__ == '__main__':
    # A cold deep link can discover pages/ before the native router first runs.
    # Register the shared shell; regular imports only expose the page callable.
    from app import main
    main()
