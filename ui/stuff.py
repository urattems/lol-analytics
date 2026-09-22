"""Patch-aware observed acquisitions and exact, profile-owned source links."""
from collections import Counter
from html import escape

import streamlit as st

from analytics.stuff import DEFINITION_VERSION, ISSUE_LABELS, STATE_LABELS, load_stuff_matches, path_groups, timing_groups, state_timing_comparisons
from core.db import Database
from core.item_catalog import BASE_URL, CatalogUnavailable, fetch_catalog, load_catalogs
from ui.formatting import decimal_label, duration_label
from ui.history_navigation import open_champion_sources
from ui.page_helpers import database_revision


@st.cache_data(show_spinner=False, max_entries=12)
def cached_stuff(path, owner, patches, revision, definition_version, include_short):
    database = Database(path)
    catalogs = load_catalogs(database, list(patches))
    return catalogs, load_stuff_matches(database, owner, catalogs, include_short_games=include_short)


def _item_strip(identifiers, catalog):
    items = []
    for identifier in identifiers:
        item = catalog.items[identifier]
        items.append(f'<span class="la-item"><img src="{BASE_URL}/cdn/{catalog.version}/img/item/{identifier}.png" alt="">'
                     f'<span>{escape(item.name)}</span></span>')
    st.markdown('<div class="la-item-strip">' + '<span aria-label="puis"> → </span>'.join(items) + '</div>', unsafe_allow_html=True)


def _group_label(row, catalogs):
    ids = row.get('path', (row.get('item_id'),))
    names = ' → '.join(catalogs[row['patch']].items[i].name for i in ids)
    return f"{names} · {row['patch']} · {row['role'] or 'Rôle inconnu'} · N={row['n']}"


def _card(row, catalogs, owner, key, label):
    _item_strip(row.get('path', (row.get('item_id'),)), catalogs[row['patch']])
    st.markdown(f"**{duration_label(row['median'])}** médiane · P25 {duration_label(row['p25'])} · P75 {duration_label(row['p75'])}")
    caption = f"N = {row['n']} · patch {row['patch']} · {row['role'] or 'Rôle inconnu'}"
    if 'winrate' in row:
        caption += f" · {decimal_label(row['winrate'], 1, ' %')} WR ({row['outcome_n']} résultats connus)"
    st.caption(caption)
    if row['small_sample']:
        st.caption('⚠ Petit échantillon : moins de 5 parties. Observation, pas recommandation.')
    if st.button(f"Voir les {row['n']} matchs sources →", key=key, width='stretch'):
        open_champion_sources(owner, row['source_ids'], label + ' · ' + _group_label(row, catalogs))


def _select_group(rows, catalogs, owner, key, label):
    if not rows:
        st.info('Aucune acquisition exploitable dans cette sélection. Une absence n’est pas un timing de 0:00.')
        return
    if st.session_state.get(key + '_group') not in range(len(rows)):
        st.session_state[key + '_group'] = 0
    choice = st.selectbox('Groupe observé', range(len(rows)), key=key + '_group',
                          format_func=lambda i: _group_label(rows[i], catalogs) +
                          (' · ' + STATE_LABELS[rows[i]['state']] if key == 'champion_state' else ''))
    _card(rows[choice], catalogs, owner, key + '_sources', label)


def timing_delta_label(delta):
    """Differences under five seconds are visually near-equal, not zeroed."""
    if abs(delta) < 5:
        return 'des timings médians très proches'
    seconds = int(abs(delta) + .5)
    minutes, seconds = divmod(seconds, 60)
    amount = (f'{minutes} min' + (f' {seconds:02d}' if seconds else '')) if minutes else f'{seconds} s'
    return amount + (' plus tôt' if delta < 0 else ' plus tard')


def render_state_insight(matches, catalogs, owner, slot, queues):
    comparisons = state_timing_comparisons(matches, slot=slot)
    st.markdown('### Ce qui ressort')
    if not comparisons:
        st.info('Aucune acquisition avec un état @10 connu pour ce palier. Le détail reste disponible ci-dessous.')
        return
    key = 'champion_state_insight_group'
    if st.session_state.get(key) not in range(len(comparisons)):
        st.session_state[key] = 0
    index = st.selectbox('Objet comparable', range(len(comparisons)), key=key,
        format_func=lambda i: _group_label(dict(comparisons[i], n=len(comparisons[i]['comparable_source_ids'])), catalogs))
    row = comparisons[index]
    _item_strip((row['item_id'],), catalogs[row['patch']])
    st.caption(f"{row['champion']} · {row['role'] or 'Rôle inconnu'} · patch {row['patch']} · "
               + ('Bottes T2' if slot == 0 else f'Item majeur {slot}') + ' · ' + queues)
    if row['sufficient_sample']:
        delta = timing_delta_label(row['delta_seconds'])
        wording = (f'les timings médians de cet achat sont très proches de ceux des parties en retard ({row["delta_seconds"]:+.1f} s).'
                   if abs(row['delta_seconds']) < 5 else f'cet achat comparable est observé **{delta}** que dans les parties en retard.')
        st.markdown('Dans les parties où vous êtes en avance @10, ' + wording)
    else:
        st.info('Échantillon insuffisant pour comparer les parties en avance et en retard : au moins 5 acquisitions dans chaque groupe sont nécessaires.')
    for column, state in zip(st.columns(2), ('ahead', 'behind')):
        group = row[state]
        with column:
            st.metric(STATE_LABELS[state] + ' · médiane', duration_label(group['median']) if group else 'N/A')
            st.caption(f'N = {group["n"] if group else 0}' +
                       (f' · P25 {duration_label(group["p25"])} · P75 {duration_label(group["p75"])}' if group else ''))
    st.caption('Observation descriptive. L’état @10 peut être observé après l’achat de l’item. Aucune causalité déduite.')
    ids = row['comparable_source_ids']
    if st.button(f'Voir les {len(ids)} matchs sources →', key='champion_state_insight_sources'):
        open_champion_sources(owner, ids, 'Avance / retard @10 · ' + _group_label(dict(row, n=len(ids)), catalogs))


def render_stuff(database, owner, games):
    patches = tuple(sorted(games['patch'].drop_nulls().unique().to_list()))
    catalogs, facts = cached_stuff(str(database.path), owner, patches, database_revision(database.path),
                                 DEFINITION_VERSION, bool(st.session_state.get('champions_include_short_games', False)))
    selected_ids = games['match_id'].to_list()
    selected = [facts[i] for i in selected_ids if i in facts]
    eligible = [fact for fact in selected if fact.eligible]
    coverage_area = st.container(horizontal=True, vertical_alignment='center')
    coverage_area.caption(f"{len(eligible)} / {len(selected_ids)} parties exploitables · {len(selected)} Timelines · {len(catalogs)} / {len(patches)} catalogues de patch")
    missing = [p for p in patches if p not in catalogs]
    if missing:
        st.info('Les horaires nécessitent le catalogue du patch joué. Sans lui, aucun item complet n’est deviné.')
        with st.container(horizontal=True, vertical_alignment='bottom'):
            patch = st.selectbox('Catalogue manquant', missing, key='champion_catalog_patch')
            if st.button('Charger ce catalogue', key='champion_catalog_fetch', icon=':material/download:'):
                try:
                    with st.spinner('Lecture de Data Dragon · aucune clé Riot nécessaire…'):
                        fetch_catalog(database, patch)
                except CatalogUnavailable as error:
                    st.warning(str(error))
                except Exception as error:
                    from ui.errors import show_diagnostic
                    show_diagnostic(error, phase='render')
                    st.warning('Catalogue indisponible. Les données locales sont conservées ; vous pouvez réessayer.')
                else:
                    cached_stuff.clear()
                    st.rerun()
    reasons = Counter(issue for fact in selected for issue in fact.issues)
    reasons['missing_timeline'] += len(selected_ids) - len(selected)
    with coverage_area.popover('Couverture & règles'):
        st.write('Achats Timeline conservés après annulation. Les ventes n’effacent pas un achat passé ; '
                 'un rachat du même objet ne crée pas un nouveau palier. Bottes T2 séparées des items majeurs. '
                 'Composants, consommables et transformations gratuites ne sont pas des achats majeurs.')
        st.caption('Médiane et quartiles sur les acquisitions observées uniquement. Groupes séparés par champion, rôle, patch et objet. '
                   'Les files peuvent être réunies si « Toutes » est sélectionné. N varie par palier ; pas de temps d’achat estimé à partir du gold.')
        if any(reasons.values()):
            st.dataframe([{'Motif d’exclusion': ISSUE_LABELS[key], 'Parties': n} for key, n in reasons.items() if n], hide_index=True, width='stretch')
            st.caption('Plusieurs motifs peuvent concerner la même partie. Couverture des frames requise du début à la fin, sans trou de plus de 90 secondes.')
        for patch, catalog in catalogs.items():
            from datetime import datetime, timezone
            st.caption(f"Patch {patch} · Data Dragon {catalog.version} · récupéré le {datetime.fromtimestamp(catalog.fetched_at, timezone.utc):%d/%m/%Y %H:%M UTC}")
    if not eligible:
        st.info('Pas encore de timings qualifiés. Consultez les motifs ci-dessus ou ouvrez une partie pour sa Timeline.')
        return
    view = st.radio('Lire mes achats', ['Timings', 'État personnel @10', 'Chemins d’objets'], horizontal=True, key='champion_stuff_view', label_visibility='collapsed')
    if view == 'Timings':
        st.caption('Chaque carte décrit un objet réellement acheté, pas le « meilleur build ». Changez le groupe pour voir les variantes.')
        for columns, slots in ((st.columns(2), (1, 0)), (st.columns(2), (2, 3))):
            for column, slot in zip(columns, slots):
                label = {0: 'Bottes T2', 1: 'Premier item majeur', 2: 'Deuxième item majeur', 3: 'Troisième item majeur'}[slot]
                with column, st.container(border=True):
                    st.markdown('#### ' + label)
                    _select_group(timing_groups(eligible, slot=slot), catalogs, owner, f'champion_timing_{slot}', label)
    elif view == 'État personnel @10':
        st.caption('Gold personnel − adversaire unique du même rôle déclaré à 10 min. Avance ≥ +500 ; retard ≤ −500 ; équilibré entre les deux. '
                   'Ce n’est ni le gold d’équipe ni une mesure de causalité : certains achats précèdent 10 min.')
        counts = Counter(f.personal_state_10 for f in eligible)
        st.write(' · '.join(f'{label} : {counts[state]}' for state, label in STATE_LABELS.items()))
        slot = st.selectbox('Palier observé', [1, 0, 2, 3], format_func=lambda v: 'Bottes T2' if v == 0 else f'Item majeur {v}', key='champion_state_slot')
        from core.queues import queue_name
        queue_ids = games['queue_id'].drop_nulls().unique().to_list()
        queue_scope = 'Files observées : ' + ', '.join(queue_name(q) for q in sorted(queue_ids))
        render_state_insight(eligible, catalogs, owner, slot, queue_scope)
        rows = timing_groups(eligible, slot=slot, by_state=True)
        st.dataframe([{'Objet / patch / rôle': _group_label(row, catalogs), 'État @10': STATE_LABELS[row['state']],
                       'N': row['n'], 'Médiane': duration_label(row['median']), 'P25': duration_label(row['p25']), 'P75': duration_label(row['p75'])}
                      for row in rows], hide_index=True, width='stretch')
        _select_group(rows, catalogs, owner, 'champion_state', 'Timing par état personnel @10')
    else:
        left, right = st.columns(2)
        length = left.selectbox('Longueur du chemin', [2, 3], key='champion_path_length')
        state = right.selectbox('État personnel @10', ['all', 'ahead', 'even', 'behind', 'unknown'],
                                format_func=lambda v: 'Tous les états' if v == 'all' else STATE_LABELS[None if v == 'unknown' else v], key='champion_path_state')
        rows = path_groups(eligible, length=length, state=None if state == 'unknown' else state)
        st.caption('Ordre des items majeurs distincts, sans bottes. Timing = achat du dernier item du chemin. '
                   'Fréquences et WR descriptifs : parties courtes, état de jeu et choix du joueur influencent les résultats.')
        _select_group(rows, catalogs, owner, 'champion_path', f'Chemin de {length} items')
        with st.expander('Tous les chemins observés'):
            st.dataframe([{'Chemin / patch / rôle': _group_label(row, catalogs), 'N': row['n'],
                           'WR': decimal_label(row['winrate'], 1, ' %'), 'Médiane': duration_label(row['median']),
                           'P25': duration_label(row['p25']), 'P75': duration_label(row['p75'])} for row in rows], hide_index=True, width='stretch')
