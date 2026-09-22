"""Personal comparisons and dated rank observations; opening this page is offline."""
import streamlit as st

from analytics.progression import ProgressionScope, compare_windows
from core.db import Database
from core.queues import queue_name
from ui.components import page_header, empty_state
from ui.page_helpers import cached_context_dataset, database_revision
from ui.profiles import get_active_settings, get_active_player
from ui.progression import render_comparison, render_timing_comparison, scope_label
from ui.profile_ranks import render_profile_ranks

WIDGETS = ('progression_champion', 'progression_role', 'progression_queue', 'progression_window',
           'progression_patch_policy', 'progression_patch', 'progression_short', 'progression_section',
           'progression_metric', 'progression_timing_slot', 'progression_timing_group', 'progression_rank_queue')
WINDOWS = {'10 récentes / 20 précédentes': (10, 20), '20 / 20': (20, 20), '20 / 50': (20, 50)}


def remember():
    st.session_state['progression_saved_widgets'] = {**st.session_state.get('progression_saved_widgets', {}),
        **{key: st.session_state[key] for key in WIDGETS if key in st.session_state}}


def _choice(area, label, values, key, formatter=str, default=None):
    choices = ['__all__', *values]
    if st.session_state.get(key) not in choices:
        st.session_state[key] = default if default in values else '__all__'
    selected = area.selectbox(label, choices, key=key, format_func=lambda v: 'Tous' if v == '__all__' else formatter(v))
    return None if selected == '__all__' else selected


def show_progression():
    settings = get_active_settings()
    database = Database(settings.database_path)
    player = get_active_player(database, settings)
    page_header('Qu’est-ce qui change dans votre jeu ?', 'Deux périodes distinctes. Des repères personnels, avec leurs preuves.', 'PROGRESSION')
    if not player:
        empty_state('Un profil pour commencer', 'Enregistrez un profil dans la bibliothèque. Aucune collecte Riot n’est lancée en ouvrant cette page.')
        return
    owner = str(player['puuid'])
    for key, value in st.session_state.get('progression_saved_widgets', {}).items():
        if key not in st.session_state:
            st.session_state[key] = value
    labels = ['Comparaison', 'Achats & timings', 'Rang daté']
    if st.session_state.get('progression_section') not in labels:
        st.session_state['progression_section'] = labels[0]
    tabs = st.tabs(labels, key='progression_section', on_change=remember)
    remember()
    if tabs[2].open:
        with tabs[2]:
            render_profile_ranks(database, settings, owner)
        remember()
        return
    # Personal ranks have their own queue/server chronology. Do not show
    # irrelevant match filters or load Timeline analysis on that tab.
    with tabs[0] if tabs[0].open else tabs[1]:
        _render_match_comparison(database, owner, tabs[1].open)
    remember()


def _render_match_comparison(database, owner, timing):
    games = cached_context_dataset(str(database.path), owner, database_revision(database.path))
    top = games.group_by(['champion', 'role']).len().sort(['len', 'champion', 'role'], descending=[True, False, False]).row(0, named=True) if not games.is_empty() else {}
    columns = st.columns(3)
    champions = sorted(games['champion'].drop_nulls().unique().to_list())
    champion = _choice(columns[0], 'Champion', champions, 'progression_champion', default=top.get('champion'))
    role = _choice(columns[1], 'Rôle', sorted(games['role'].drop_nulls().unique().to_list()), 'progression_role', default=top.get('role'))
    queue = _choice(columns[2], 'File', sorted(games['queue_id'].drop_nulls().unique().to_list()), 'progression_queue', queue_name)
    columns = st.columns([1.1, 1.5, 1])
    window = columns[0].selectbox('Fenêtres', list(WINDOWS), key='progression_window')
    policy = columns[1].selectbox('Politique de patch', ['latest', 'specific', 'mixed'], key='progression_patch_policy',
        format_func=lambda p: {'latest': 'Dernier patch présent · homogène', 'specific': 'Un patch précis', 'mixed': 'Mélanger les patchs explicitement'}[p])
    patch = None
    if policy == 'specific':
        patches = sorted(games['patch'].drop_nulls().unique().to_list())
        if patches:
            if st.session_state.get('progression_patch') not in patches:
                st.session_state['progression_patch'] = patches[-1]
            patch = columns[2].selectbox('Patch précis', patches, key='progression_patch')
        else:
            st.info('Aucun patch connu. La comparaison reste vide ; le rang daté est toujours accessible.')
            policy = 'latest'
    include = st.checkbox('Inclure les parties de moins de 5 minutes', key='progression_short')
    scope = ProgressionScope(champion, role, queue, policy, patch, include, *WINDOWS[window])
    comparison = compare_windows(games, scope)
    st.caption(scope_label(scope, comparison.effective_patch) + ' · cohortes filtrées avant découpage, dates manquantes exclues des périodes')
    if timing:
        render_timing_comparison(comparison, database, owner)
    else:
        render_comparison(comparison, owner)


if __name__ == '__main__':
    from app import main
    main()
