"""Reusable comparison views: explicit scope, denominators and exact sources."""
from html import escape

import plotly.graph_objects as go
import polars as pl
import streamlit as st

from analytics.exports import markdown_text
from analytics.progression import METRICS, compare_metrics, compare_timings, metric_value, window_context, comparison_highlights
from ui.charts import chart_style, match_tick_step
from ui.formatting import decimal_label, duration_label, match_date_label
from ui.history_navigation import open_match_sources
from ui.theme import TEAL, GOLD


def source_button(owner, ids, label, key, *, origin='progression'):
    if st.button(f'Voir les {len(ids)} matchs sources →', key=key, disabled=not ids, width='stretch'):
        open_match_sources(owner, ids, label, origin=origin)


def scope_label(scope, patch):
    return ' · '.join([scope.champion or 'Tous champions', scope.role or 'Tous rôles',
                       f'File {scope.queue_id}' if scope.queue_id else 'Toutes files',
                       f'Patch {patch}' if patch else 'Patchs mélangés / indisponibles'])


def value_label(value, key):
    if key == 'duration':
        return duration_label(value)
    return decimal_label(value, 0 if key.startswith('gold_') else 2, ' %' if key == 'winrate' else '')


def render_window(games, title, requested, owner, key):
    context = window_context(games)
    st.markdown(f'#### {title} · {context["n"]} / {requested} parties')
    st.caption(f'{match_date_label(context["first"], True)} → {match_date_label(context["last"], True)}')
    st.caption('Patchs : ' + (', '.join(context['patches']) or 'indisponibles') +
               (f' · {context["unknown_patch"]} sans patch' if context['unknown_patch'] else '') +
               f' · Timelines {context["timeline_n"]}/{context["n"]}')
    source_button(owner, games['match_id'].to_list() if 'match_id' in games.columns else [], title, key)


def trend_figure(comparison, key):
    """Chronological observations, no synthetic dates; patch changes are marked."""
    frames = [comparison.previous, comparison.recent]
    rows = []
    for frame, period in zip(frames, ('Précédent', 'Récent')):
        rows.extend([{**r, 'period': period} for r in frame.reverse().to_dicts()])
    figure = go.Figure()
    for period, color in (('Précédent', GOLD), ('Récent', TEAL)):
        points = [(i+1, row, metric_value(row, key)) for i, row in enumerate(rows) if row['period'] == period]
        figure.add_trace(go.Scatter(x=[i for i, _, _ in points], y=[value for _, _, value in points],
            mode='markers', name=period, marker={'color': color, 'size': 8},
            customdata=[[match_date_label(row['game_creation'], True), escape(row.get('patch') or 'N/A')] for _, row, _ in points],
            hovertemplate='%{customdata[0]} · patch %{customdata[1]}<br>%{y:.2f}<extra>%{fullData.name}</extra>'))
    boundary = comparison.previous.height
    if boundary and comparison.recent.height:
        figure.add_vline(x=boundary+.5, line_dash='dash', line_color=TEAL, annotation_text='Récent →')
    for index in range(1, len(rows)):
        if rows[index].get('patch') != rows[index-1].get('patch'):
            figure.add_vline(x=index+.5, line_dash='dot', line_color=GOLD)
    figure.update_xaxes(title='Parties · ancien → récent · pointillé or = changement de patch', dtick=match_tick_step(len(rows)))
    figure.update_yaxes(title=METRICS[key].label)
    return chart_style(figure, 285)


def render_comparison(comparison, owner):
    recent, previous = window_context(comparison.recent), window_context(comparison.previous)
    if comparison.scope.patch_policy == 'mixed':
        st.warning('Mélange de patchs choisi explicitement. Un changement après patch n’est pas une amélioration personnelle certaine.')
    if comparison.undated_count or comparison.excluded_patch_count:
        st.caption(f'{comparison.undated_count} partie(s) sans date exploitable écartée(s) des périodes ; '
                   f'{comparison.excluded_patch_count} hors du patch retenu. Elles restent dans l’historique.')
    left, right = st.columns(2)
    with left, st.container(border=True):
        render_window(comparison.previous, 'Précédent', comparison.scope.previous_count, owner, 'progression_previous_sources')
    with right, st.container(border=True):
        render_window(comparison.recent, 'Récent', comparison.scope.recent_count, owner, 'progression_recent_sources')
    if not recent['n']:
        st.info('Pas de parties datées dans cette cohorte. Choisissez un autre patch ou élargissez les filtres.')
        return
    if min(recent['n'], previous['n']) < 10:
        st.info('Historique limité : moins de 10 parties dans au moins une fenêtre. Ces écarts sont des observations, pas une conclusion de progression.')
    pairs = compare_metrics(comparison)
    highlights = comparison_highlights(pairs)
    st.markdown('### Ce qui change dans cette sélection')
    from core.queues import queue_name
    st.caption(scope_label(comparison.scope, comparison.effective_patch) + ' · '
               + (queue_name(comparison.scope.queue_id) if comparison.scope.queue_id else 'Toutes files')
               + (' · Comparaison multi-patch' if comparison.scope.patch_policy == 'mixed' else ' · Patch homogène'))
    if not highlights:
        st.info('Pas assez de valeurs comparables pour mettre un écart en avant : 10 valeurs exploitables par période sont nécessaires. Le détail reste disponible.')
    else:
        for column, highlight in zip(st.columns(len(highlights)), highlights):
            key = highlight['key']
            with column, st.container(border=True):
                st.markdown('**' + METRICS[key].label + '**')
                st.markdown(value_label(highlight['previous']['value'], key) + ' → **' + value_label(highlight['recent']['value'], key) + '**')
                st.caption(f"Écart récent : {highlight['delta']:+.2f} {METRICS[key].unit or METRICS[key].label}")
                st.caption(f"N précédent = {highlight['previous']['n']} · N récent = {highlight['recent']['n']}")
                ids = tuple(dict.fromkeys(highlight['previous']['source_ids'] + highlight['recent']['source_ids']))
                source_button(owner, ids, METRICS[key].label + ' · variation observée', 'progression_highlight_' + key)
        st.caption('Variations descriptives, sans score global ni conclusion automatique de progression. Précédent → récent.')
    st.markdown('### Toutes les métriques')
    st.dataframe([{'Métrique': metric.label, 'Résumé': 'Médiane' if metric.method == 'median' else 'Moyenne',
                   'Précédent': value_label(pairs[key]['previous']['value'], key), 'N précédent': pairs[key]['previous']['n'],
                   'Récent': value_label(pairs[key]['recent']['value'], key), 'N récent': pairs[key]['recent']['n'],
                   'Écart absolu': decimal_label(pairs[key]['delta'], 2)} for key, metric in METRICS.items()],
                  width='stretch', hide_index=True)
    st.caption('Écart = récent − précédent, en unités de la métrique (points pour WR), pas en % de variation. '
               'KDA = moyenne de (kills + assists) / max(1, morts). Rythmes par minute : parties avec durée positive et donnée connue uniquement.')
    key = st.selectbox('Métrique à examiner', list(METRICS), format_func=lambda k: METRICS[k].label, key='progression_metric')
    pair = pairs[key]
    columns = st.columns(2)
    for column, period, label in zip(columns, ('previous', 'recent'), ('Précédent', 'Récent')):
        sample = pair[period]
        with column:
            st.caption(f'{label} · N={sample["n"]} · P25 {value_label(sample["p25"], key)} · P75 {value_label(sample["p75"], key)}')
            source_button(owner, sample['source_ids'], f'{METRICS[key].label} · {label} · valeurs exploitables', 'progression_metric_' + period)
    st.plotly_chart(trend_figure(comparison, key), width='stretch', config={'displayModeBar': False})
    if comparison.scope.patch_policy == 'mixed':
        with st.expander('Ventilation par patch'):
            rows = []
            from analytics.progression import summarize_metric
            for frame, label in ((comparison.previous, 'Précédent'), (comparison.recent, 'Récent')):
                for patch in frame['patch'].unique().to_list():
                    subset = frame.filter(pl.col('patch').is_null() if patch is None else pl.col('patch') == patch)
                    sample = summarize_metric(subset, key)
                    rows.append({'Période': label, 'Patch': patch or 'N/A', 'Parties': subset.height,
                                 'N exploitable': sample['n'], METRICS[key].label: value_label(sample['value'], key)})
            st.dataframe(rows, hide_index=True, width='stretch')


def render_timing_comparison(comparison, database, owner):
    from analytics.stuff import DEFINITION_VERSION
    from ui.stuff import cached_stuff
    from ui.page_helpers import database_revision
    frames = [comparison.recent, comparison.previous]
    patches = tuple(sorted({p for frame in frames for p in frame['patch'].drop_nulls().to_list()}))
    catalogs, facts = cached_stuff(str(database.path), owner, patches, database_revision(database.path),
                                 DEFINITION_VERSION, comparison.scope.include_short)
    st.caption('Comparaisons uniquement à champion, rôle, patch et objet identiques. Les catalogues absents se chargent explicitement depuis Champions → Stuff & timings.')
    slot = st.selectbox('Palier', [1, 0, 2, 3], format_func=lambda v: 'Bottes T2' if v == 0 else f'Item majeur {v}', key='progression_timing_slot')
    rows = compare_timings(*frames, facts, slot=slot)
    if not rows:
        st.info('Pas de timing qualifié dans ces fenêtres. Une Timeline partielle ou un catalogue absent ne vaut pas 0:00.')
        return
    def label(row):
        return f"{catalogs[row['patch']].items[row['item_id']].name} · {row['champion']} · {row['role']} · {row['patch']}"
    if st.session_state.get('progression_timing_group') not in range(len(rows)):
        st.session_state['progression_timing_group'] = 0
    selected = st.selectbox('Groupe comparable', range(len(rows)), format_func=lambda i: label(rows[i]), key='progression_timing_group')
    row = rows[selected]
    st.markdown('**' + markdown_text(label(row)) + '**')
    for column, period, title in zip(st.columns(2), ('previous', 'recent'), ('Précédent', 'Récent')):
        with column, st.container(border=True):
            sample = row[period]
            if sample:
                st.metric(title, duration_label(sample['median']))
                st.caption(f'N={sample["n"]} · P25 {duration_label(sample["p25"])} · P75 {duration_label(sample["p75"])}')
                if sample['small_sample']:
                    st.caption('Petit échantillon : moins de 5 acquisitions.')
                source_button(owner, sample['source_ids'], label(row) + ' · ' + title, 'progression_timing_' + period)
            else:
                st.info(f'{title} : aucune acquisition comparable, N=0.')
    st.caption(f'Écart médian récent − précédent : {decimal_label(row["delta"], 1, " s")} · descriptif, pas une mesure d’efficacité du build.')
