"""Personal journal: explicit writes and sharing; no implicit Riot collection."""
import streamlit as st

from analytics.exports import markdown_text
from analytics.explorer import ExplorerSelection
from analytics.journal import recurring_players
from core.db import Database
from core.journal import load_annotations
from ui.components import page_header, empty_state
from ui.formatting import match_date_label
from ui.goals import render_goals
from ui.history_navigation import cached_history_library, open_match_sources
from ui.journal import render_notes
from ui.page_helpers import database_revision
from ui.profiles import get_active_player, get_active_settings
from ui.sharing import render_sharing

SECTIONS = ['Notes & tags', 'Objectifs', 'Joueurs retrouvés', 'Partager']
SAVED = ('journal_section', 'journal_match', 'journal_tag_selected', 'journal_goal_selected', 'journal_goal_archived',
         'journal_scope_window', 'journal_scope_champion', 'journal_scope_role', 'journal_scope_patch', 'journal_scope_queue',
         'journal_scope_short', 'journal_players_relation', 'journal_players_minimum', 'journal_player_selected')


def remember():
    st.session_state['journal_saved_widgets'] = {**st.session_state.get('journal_saved_widgets', {}),
        **{k: st.session_state[k] for k in SAVED if k in st.session_state}}


def _selection(library):
    windows = {'20 dernières': 20, '50 dernières': 50, '100 dernières': 100, 'Tout l’historique': None}
    window = st.selectbox('Fenêtre du journal', list(windows), key='journal_scope_window')
    fields = {}
    for area, label, field in zip(st.columns(4), ('Champion', 'Rôle', 'Patch', 'File'), ('champion', 'role', 'patch', 'queue_id')):
        values = ['__all__', *sorted(library.games[field].drop_nulls().unique().to_list())]
        key = 'journal_scope_' + ('queue' if field == 'queue_id' else field)
        if st.session_state.get(key) not in values: st.session_state[key] = '__all__'
        value = area.selectbox(label, values, format_func=lambda v: 'Tous' if v == '__all__' else str(v), key=key)
        fields[field] = None if value == '__all__' else value
    include = st.checkbox('Inclure les parties de moins de 5 minutes', key='journal_scope_short')
    selected = library.select(ExplorerSelection(base_game_limit=windows[window], include_short_games=include, **fields))
    st.caption(f'{selected.height} parties sélectionnées · fenêtre puis filtres, comme Historique. Cette sélection ne modifie pas le périmètre des objectifs.')
    return selected


def render_players(library, selected):
    relation = st.selectbox('Relation observée', ['ally', 'enemy'], format_func=lambda v: 'Même équipe' if v == 'ally' else 'Équipe adverse', key='journal_players_relation')
    minimum = st.selectbox('Rencontres minimum dans cette sélection', [2, 3, 5, 10], key='journal_players_minimum')
    rows = recurring_players(library, selected, relation, minimum)
    st.caption('Des joueurs croisés dans vos parties, pas une détection de premade ou de duo. Aucun profil croisé n’est importé automatiquement. Les V/D sont ceux du profil analysé.')
    if not rows:
        st.info('Aucun joueur ne dépasse ce seuil dans la sélection.')
        return
    st.dataframe([{'Joueur local': library.identities[r['puuid']].local_label, 'N': r['n'], 'V': r['wins'], 'D': r['losses'],
        'Dernière rencontre': match_date_label(r['last_seen'], True), 'Champions observés': ', '.join(f'{name} ({n})' for name, n in r['champions']),
        'Rôles observés': ', '.join(f'{name} ({n})' for name, n in r['roles'])} for r in rows], hide_index=True, width='stretch')
    ids = [r['puuid'] for r in rows]
    if st.session_state.get('journal_player_selected') not in ids: st.session_state['journal_player_selected'] = ids[0]
    player = st.selectbox('Joueur à retrouver', ids, format_func=lambda i: library.identities[i].local_label, key='journal_player_selected')
    row = next(r for r in rows if r['puuid'] == player)
    st.button(f'Voir les {row["n"]} rencontres sélectionnées →', key='journal_player_sources',
              on_click=open_match_sources,
              args=(library.owner, row['source_ids'], 'Rencontres dans le journal · ' + ('allié' if relation == 'ally' else 'adversaire')),
              kwargs={'origin': 'journal'})


def show_journal():
    settings = get_active_settings()
    database = Database(settings.database_path)
    player = get_active_player(database, settings)
    page_header('Ce que vous voulez retenir.', 'Notes privées, objectifs choisis et rencontres à retrouver. Vous décidez de ce qui se partage.', 'JOURNAL PERSONNEL')
    if not player:
        empty_state('Un profil pour votre journal', 'Enregistrez un profil dans la bibliothèque. Aucune note ni collecte n’est créée en ouvrant cette page.')
        return
    owner = str(player['puuid'])
    for key, value in st.session_state.get('journal_saved_widgets', {}).items():
        if key not in st.session_state: st.session_state[key] = value
    if flash := st.session_state.pop('journal_flash', None): st.success(flash)
    if st.session_state.get('journal_section') not in SECTIONS: st.session_state['journal_section'] = SECTIONS[0]
    tabs = st.tabs(SECTIONS, key='journal_section', on_change=remember)
    remember()
    library = cached_history_library(str(database.path), owner, database_revision(database.path))
    if tabs[0].open:
        with tabs[0]: render_notes(database, owner, library, load_annotations(database, owner))
    if tabs[1].open:
        with tabs[1]: render_goals(database, owner, library.games)
    if tabs[2].open:
        with tabs[2]: render_players(library, _selection(library))
    if tabs[3].open:
        with tabs[3]: render_sharing(database, owner, _selection(library))
    remember()


if __name__ == '__main__':
    from app import main
    main()
