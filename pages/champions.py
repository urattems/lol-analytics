"""Champion analytics page."""

from __future__ import annotations

from html import escape

import plotly.graph_objects as go
import polars as pl
import streamlit as st

from analytics.builds import build_summary
from analytics.champions import TREND_METRICS, champion_detail_summary, champion_matches, champion_trend
from analytics.insights import build_insight_bundle
from analytics.overview import champion_summary
from ui.profiles import get_active_settings, get_active_player
from core.db import Database
from core.static_data import DataDragonService, get_static_data_service
from ui.components import render_metric_grid, page_header
from ui.charts import chart_style, match_tick_step
from ui.formatting import decimal_label, duration_label, kda_line, match_date_label
from ui.page_helpers import load_selected_dataset
from ui.static_assets import render_item_strip
from ui.theme import GREEN, MUTED, RED, TEAL


def _champion_table(games: object, static_data: DataDragonService) -> None:
    summary = champion_summary(games)  # type: ignore[arg-type]
    display = []
    for row in summary.to_dicts():
        display.append(
            {
                "Champion": static_data.champion(str(row["champion"])).display_name,
                "Games": row["games"],
                "Wins": row["wins"],
                "Losses": row["losses"],
                "Winrate": decimal_label(row["winrate"], 1, " %"),
                "KDA": decimal_label(row["kda"], 2),
                "Kills moy.": decimal_label(row["average_kills"], 1),
                "Deaths moy.": decimal_label(row["average_deaths"], 1),
                "Assists moy.": decimal_label(row["average_assists"], 1),
                "CS/min": decimal_label(row["cs_per_minute"], 2),
                "Gold/min": decimal_label(row["gold_per_minute"], 0),
                "Damage/min": decimal_label(row["damage_per_minute"], 0),
                "Vision/min": decimal_label(row["vision_per_minute"], 2),
                "Rôle principal": row["primary_role"] or "N/A",
            }
        )
    st.dataframe(display, width="stretch", hide_index=True)


def _trend_figure(trend: object, metric: str) -> go.Figure:
    rows = trend.to_dicts()  # type: ignore[union-attr]
    results = ["WIN" if row["win"] == 1 else "LOSS" if row["win"] == 0 else "N/A" for row in rows]
    colors = [GREEN if result == "WIN" else RED if result == "LOSS" else MUTED for result in results]
    custom = [
        [
            match_date_label(row.get("game_creation"), True),
            results[index],
            kda_line(row.get("kills"), row.get("deaths"), row.get("assists")),
            duration_label(row.get("duration")),
        ]
        for index, row in enumerate(rows)
    ]
    figure = go.Figure(
        go.Scatter(
            x=[row["match_number"] for row in rows],
            y=[row["metric_value"] for row in rows],
            mode="lines+markers",
            line={"color": TEAL, "width": 2.5},
            marker={"color": colors, "size": 10},
            customdata=custom,
            hovertemplate=(
                "%{customdata[0]} · <b>%{customdata[1]}</b><br>"
                "K/D/A : %{customdata[2]}<br>Durée : %{customdata[3]}<br>"
                + metric
                + " : %{y:.2f}<extra></extra>"
            ),
        )
    )
    figure.update_layout(
        height=390,
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(8,19,32,.55)",
        font={"color": "#b8c7d5"},
        xaxis={"title": "Matchs · ancien → récent", "dtick": 1, "gridcolor": "rgba(143,163,184,.10)"},
        yaxis={"title": metric, "rangemode": "tozero", "gridcolor": "rgba(143,163,184,.10)"},
        showlegend=False,
    )
    figure.update_xaxes(dtick=match_tick_step(len(rows)))
    return chart_style(figure, 300)


CHAMPION_WIDGETS = (
    'champion_selected', 'champion_role', 'champion_patch', 'champion_queue', 'champion_section',
    'champions_dataset', 'champions_custom_limit', 'champions_include_short_games', 'champion_trend_metric',
    'champion_stuff_view', 'champion_state_slot', 'champion_path_length', 'champion_path_state',
    'champion_timing_0_group', 'champion_timing_1_group', 'champion_timing_2_group', 'champion_timing_3_group',
    'champion_state_group', 'champion_state_insight_group', 'champion_path_group', 'champion_matchup_source',
    'champion_matchup_metric', 'champion_matchup_timing_group',
)


def _remember():
    previous = st.session_state.get('champion_saved_widgets', {})
    st.session_state['champion_saved_widgets'] = {
        **previous, **{key: st.session_state[key] for key in CHAMPION_WIDGETS if key in st.session_state}
    }


def _restore():
    for key, value in st.session_state.get('champion_saved_widgets', {}).items():
        if key not in st.session_state:
            st.session_state[key] = value


def _choose(area, label, options, key, formatter=str, default=None):
    # A None widget value means "no selection" to Streamlit, even when None is
    # an option. Use an internal sentinel so "Toutes" remains visibly selected.
    choices = ['__all__' if value is None else value for value in options]
    fallback = default if default in options else options[0]
    if key not in st.session_state or st.session_state.get(key) not in choices:
        st.session_state[key] = '__all__' if fallback is None else fallback
    value = area.selectbox(label, choices, key=key, format_func=lambda v: formatter(None if v == '__all__' else v))
    return None if value == '__all__' else value


def _summary(selected_games, selected, detail):
    render_metric_grid([
        ('Gold/min', decimal_label(detail['gold_per_minute'], 0)),
        ('Damage/min', decimal_label(detail['damage_per_minute'], 0)),
        ('Vision/min', decimal_label(detail['vision_per_minute'], 2)),
    ], columns=3)
    metric = st.selectbox('Métrique', list(TREND_METRICS), key='champion_trend_metric', on_change=_remember)
    st.plotly_chart(_trend_figure(champion_trend(selected_games, selected, metric), metric),
                    width='stretch', config={'displayModeBar': False})
    with st.expander('Contextes & repères Timeline'):
        bundle = build_insight_bundle(selected_games, pl.DataFrame())
        for title, key in (('Red vs Blue', 'side'), ('Queues', 'queue'), ('Durées', 'duration'),
                           ('Patchs', 'patch'), ('Récent vs précédent', 'recent_previous')):
            st.markdown('#### ' + title)
            st.dataframe([{'Contexte': row.get('label'), 'N': row.get('games'),
                'WR': decimal_label(row.get('winrate'), 1, ' %'), 'KDA': decimal_label(row.get('kda'), 2)}
                for row in bundle[key]], width='stretch', hide_index=True)
        rows = selected_games.filter(pl.col('timeline_available') == True).to_dicts()  # noqa: E712
        st.caption(f"Timelines disponibles : {len(rows)} / {selected_games.height}")
        metrics = []
        for key, label in (('gold_diff_10', 'Gold diff @10'), ('gold_diff_15', 'Gold diff @15'),
                           ('cs_diff_10', 'CS diff @10'), ('cs_diff_15', 'CS diff @15')):
            values = [row[key] for row in rows if row.get(key) is not None]
            metrics.append((label, decimal_label(sum(values) / len(values) if values else None, 1) + f' · N={len(values)}'))
        render_metric_grid(metrics, columns=4)
        from analytics.consistency import consistency_summary
        robust = next((row for row in consistency_summary(selected_games) if row['metric'] == 'Gold Diff @15'), None)
        if robust:
            st.caption(f"Gold diff @15 médian : {decimal_label(robust['median'], 0)} · "
                       f"P25 {decimal_label(robust['p25'], 0)} · P75 {decimal_label(robust['p75'], 0)}")


def show_champions() -> None:
    """Four lazy sections; one scoped selection and durable round-trip context."""
    from core.queues import queue_name
    from ui.formatting import context_label
    from ui.page_helpers import cached_context_dataset, database_revision
    from ui.history_navigation import open_champion_sources
    from ui.stuff import render_stuff

    settings = get_active_settings()
    database = Database(settings.database_path)
    player = get_active_player(database, settings)
    page_header('Un champion. Votre façon de jouer.', 'Du repère personnel à la partie qui l’explique.', 'CHAMPIONS')
    if not player:
        st.info('Aucune partie synchronisée.')
        return
    owner = str(player['puuid'])
    _restore()
    static_data = get_static_data_service()
    # Keep the normal analysis window, custom limits and short-game opt-in, in a
    # compact toolbar instead of placing a large card gallery above the hero.
    columns = st.columns([1.6, 1, 1, 1.1, 1], vertical_alignment='bottom')
    with columns[4], st.popover('Période', icon=':material/tune:'):
        games, _ = load_selected_dataset(database, settings.database_path, owner, 'champions')
        if not games.is_empty():
            with st.expander('Tableau détaillé du pool'):
                _champion_table(games, static_data)
    if games.is_empty():
        st.info('Aucune partie dans cette sélection. Ouvrez « Période » pour l’élargir.')
        _remember()
        return
    summary = champion_summary(games)
    pool = {row['champion']: row for row in summary.to_dicts()}
    selected = _choose(columns[0], 'Champion', list(pool), 'champion_selected',
        lambda value: f"{static_data.champion(value).display_name} · {pool[value]['games']} parties · {decimal_label(pool[value]['winrate'], 0, ' %')}")
    selected_games = champion_matches(games, selected)
    roles = selected_games['role'].drop_nulls().unique().sort().to_list()
    role = _choose(columns[1], 'Rôle', [None, *roles], 'champion_role',
                   lambda v: 'Tous les rôles' if v is None else context_label(v), pool[selected]['primary_role'])
    if role:
        selected_games = selected_games.filter(pl.col('role') == role)
    patches = sorted(selected_games['patch'].drop_nulls().unique().to_list(),
                     key=lambda v: tuple(int(p) if p.isdigit() else -1 for p in v.split('.')), reverse=True)
    patch = _choose(columns[2], 'Patch', [None, *patches], 'champion_patch',
                    lambda v: 'Tous les patchs' if v is None else v, patches[0] if patches else None)
    if patch:
        selected_games = selected_games.filter(pl.col('patch') == patch)
    queues = selected_games['queue_id'].drop_nulls().unique().sort().to_list()
    queue = _choose(columns[3], 'File', [None, *queues], 'champion_queue',
                    lambda v: 'Toutes les files' if v is None else queue_name(v))
    if queue is not None:
        selected_games = selected_games.filter(pl.col('queue_id') == queue)
    if selected_games.is_empty():
        st.info('Aucune partie pour cette combinaison.')
        _remember()
        return
    selected_ids = selected_games['match_id'].to_list()
    selected_games = cached_context_dataset(str(database.path), owner, database_revision(database.path)).filter(
        pl.col('match_id').is_in(selected_ids))
    detail = champion_detail_summary(selected_games, selected)
    champion_data = static_data.champion(selected)
    gold_values = selected_games['gold_diff_15'].drop_nulls() if 'gold_diff_15' in selected_games.columns else pl.Series([], dtype=pl.Int64)
    median = gold_values.median()
    background = (
        f"linear-gradient(90deg,rgba(5,13,24,.96),rgba(5,13,24,.35)),url('{escape(champion_data.splash_url)}')"
        if champion_data.splash_url else 'linear-gradient(120deg,#17304d,#091728)'
    )
    portrait = (f'<img class="la-champion-portrait" src="{escape(champion_data.image_url)}" alt="">'
                if champion_data.image_url else '')
    st.markdown(
        f'<div class="la-champion-hero la-champion-hero-compact" style="background-image:{background}">'
        f'<div class="la-champion-heading">{portrait}<div><div class="la-eyebrow">FICHE CHAMPION · {escape(context_label(role) if role else "TOUS RÔLES")}</div>'
        f'<div class="la-display-title">{escape(champion_data.display_name.upper())}</div></div></div>'
        f'<div class="la-hero-stats"><span><b>{detail["games"]}</b> parties</span>'
        f'<span><b>{decimal_label(detail["winrate"], 1, " %")}</b> WR</span>'
        f'<span><b>{decimal_label(detail["kda"], 2)}</b> KDA</span>'
        f'<span><b>{decimal_label(detail["cs_per_minute"], 2)}</b> CS/min</span>'
        f'<span><b>{decimal_label(median, 0)}</b> Gold diff @15 médian · N={len(gold_values)}</span></div></div>',
        unsafe_allow_html=True,
    )
    labels = ['Résumé', 'Stuff & timings', 'Matchups', 'Parties']
    if st.session_state.get('champion_section') not in labels:
        st.session_state['champion_section'] = labels[0]
    tabs = st.tabs(labels, key='champion_section', on_change=_remember)
    _remember()
    if tabs[0].open:
        with tabs[0]:
            _summary(selected_games, selected, detail)
    if tabs[1].open:
        with tabs[1]:
            render_stuff(database, owner, selected_games)
            with st.expander('Inventaires finaux · détail complémentaire'):
                st.caption('Inventaire à la fin du match, pas un ordre d’achat. Build Lab reste disponible dans les analyses complémentaires.')
                for row in build_summary(selected_games).head(5).to_dicts():
                    render_item_strip(row['items'], static_data)
                    st.caption(f"N = {row['games']} · {decimal_label(row['winrate'], 1, ' %')} WR · {decimal_label(row['kda'], 2)} KDA")
    if tabs[2].open:
        with tabs[2]:
            from ui.champion_matchups import render_champion_matchups
            render_champion_matchups(selected_games, database, owner)
            if st.button(f'Explorer les contextes de {selected}', key='champion_open_contexts'):
                from pages.contexts import show_contexts
                st.session_state['contexts_champion'] = selected
                st.query_params['champion'] = selected
                st.switch_page(st.Page(show_contexts, title='Contexts', url_path='contexts'))
    if tabs[3].open:
        with tabs[3]:
            st.caption(f'{selected_games.height} matchs de cette fiche · même champion, rôle, patch, file et fenêtre.')
            if st.button('Ouvrir ces parties dans l’historique →', key='champion_all_sources', type='primary'):
                open_champion_sources(owner, selected_ids, f'Fiche {selected} · sélection complète')
            st.dataframe([{'Date': match_date_label(row.get('game_creation')),
                'Résultat': 'WIN' if row.get('win') == 1 else 'LOSS' if row.get('win') == 0 else 'N/A',
                'K/D/A': kda_line(row.get('kills'), row.get('deaths'), row.get('assists')),
                'Durée': duration_label(row.get('duration')), 'Patch': row.get('patch') or 'N/A',
                'Timeline': 'Disponible' if row.get('timeline_available') else 'Absente'}
                for row in selected_games.to_dicts()], width='stretch', hide_index=True)
    _remember()


if __name__ == '__main__':
    # A cold deep link can discover pages/ before the native router first runs.
    # Register the shared shell; regular imports only expose the page callable.
    from app import main
    main()
