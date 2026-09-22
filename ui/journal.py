"""Explicit local editing, optimistic conflict handling and descriptive tag views."""
import streamlit as st

from analytics.exports import markdown_text
from analytics.journal import tag_observations
from core.diagnostics import record_failure, safe_message
from core.journal import JournalConflict, TAGS, save_annotation
from ui.formatting import decimal_label, match_date_label
from ui.history_navigation import open_match_sources


def journal_error(error):
    if isinstance(error, (JournalConflict, ValueError)):
        st.error(str(error))
    else:
        record_failure(error, phase='journal')
        st.error(safe_message(error))


def _reset_editor():
    st.session_state.pop('journal_editor_context', None)


def render_annotation_editor(database, owner, match_id, annotations):
    current = annotations.get(match_id, {'body': '', 'tags': [], 'revision': 0})
    if st.session_state.get('journal_editor_context') != (owner, match_id) or 'journal_note_body' not in st.session_state or 'journal_note_tags' not in st.session_state:
        st.session_state['journal_editor_context'] = (owner, match_id)
        st.session_state['journal_note_body'] = current['body']
        st.session_state['journal_note_tags'] = ', '.join(current['tags'])
        st.session_state['journal_note_revision'] = current['revision']
        st.session_state['journal_note_delete_confirm'] = False
    st.caption('Notes privées, locales et jamais envoyées à Riot. Sauvegarde sur bouton uniquement. Un changement de partie, d’onglet ou de profil peut abandonner le brouillon non enregistré.')
    body = st.text_area('Votre note', max_chars=5000, height=160, key='journal_note_body')
    tags_text = st.text_input('Tags séparés par des virgules · 10 maximum', max_chars=410, key='journal_note_tags')
    st.caption('Suggestions : ' + ', '.join(TAGS) + '. Un tag « duo » est votre annotation, pas une détection automatique.')
    tags = [tag.strip() for tag in tags_text.split(',') if tag.strip()]
    expected = st.session_state['journal_note_revision']
    if current['revision'] != expected:
        st.warning('Une autre fenêtre a modifié cette annotation. Votre brouillon reste affiché ; comparez-le à la version enregistrée.')
        with st.expander('Version enregistrée'):
            st.text(current['body'])
            st.text(', '.join(current['tags']))
    left, right = st.columns(2)
    if left.button('Enregistrer la note et les tags', key='journal_note_save', width='stretch'):
        try:
            revision = save_annotation(database, owner, match_id, body, tags, expected)
        except Exception as error:
            journal_error(error)
        else:
            st.session_state['journal_note_revision'] = revision
            st.session_state['journal_flash'] = 'Note et tags enregistrés localement.'
            st.rerun()
    right.button('Recharger la version enregistrée', key='journal_note_reload', on_click=_reset_editor, width='stretch')
    with st.expander('Effacer cette annotation'):
        confirmed = st.checkbox('Effacer la note et tous les tags de ce profil pour cette partie', key='journal_note_delete_confirm')
        if st.button('Effacer note et tags', key='journal_note_delete', disabled=not confirmed):
            try:
                save_annotation(database, owner, match_id, '', [], expected)
            except Exception as error:
                journal_error(error)
            else:
                _reset_editor()
                st.session_state['journal_flash'] = 'Note et tags effacés. La partie et les annotations des autres profils sont conservées.'
                st.rerun()


def render_notes(database, owner, library, annotations):
    games = library.games
    if games.is_empty():
        st.info('Importez une partie de ce profil pour y attacher une note. Les objectifs libres restent disponibles.')
        return
    requested = st.session_state.pop('journal_requested_match', None)
    ids = games['match_id'].to_list()
    if requested:
        if requested.get('owner') == owner and requested.get('match_id') in ids:
            st.session_state['journal_match'] = requested['match_id']
        else:
            st.warning('La partie demandée ne fait plus partie de ce profil.')
    if st.session_state.get('journal_match') not in ids:
        st.session_state['journal_match'] = ids[0]
    labels = {row['match_id']: f'{match_date_label(row.get("game_creation"), True)} · {row.get("champion") or "N/A"} · {"WIN" if row.get("win") == 1 else "LOSS" if row.get("win") == 0 else "N/A"}' for row in games.to_dicts()}
    selected = st.selectbox('Partie à annoter', ids, format_func=labels.get, key='journal_match')
    st.button('Ouvrir cette partie et sa Timeline →', key='journal_note_sources',
              on_click=open_match_sources, args=(owner, [selected], 'Partie du journal'), kwargs={'origin': 'journal'})
    render_annotation_editor(database, owner, selected, annotations)
    with st.expander('Retrouver mes annotations et tags'):
        annotated = [r for r in games.to_dicts() if (note := annotations.get(r['match_id'])) and (note['body'] or note['tags'])]
        st.caption(f'{len(annotated)} parties annotées · bibliothèque entière de ce profil, sans filtre de date implicite')
        if annotated:
            st.dataframe([{'Partie': labels[r['match_id']], 'Tags': ', '.join(annotations[r['match_id']]['tags']),
                           'Note': annotations[r['match_id']]['body']} for r in annotated], hide_index=True, width='stretch')
        groups = tag_observations(games, annotations)
        if not groups:
            return
        st.dataframe([{'Tag': row['tag'], 'N': row['n'], 'V': row['wins'], 'D': row['losses'],
                       'KDA moyen': decimal_label(row['kda']['value'], 2), 'N KDA': row['kda']['n'],
                       'Morts/min': decimal_label(row['deaths']['value'], 2), 'N morts/min': row['deaths']['n']} for row in groups], hide_index=True, width='stretch')
        st.caption('Groupes manuels, parfois superposés ; association descriptive, pas « le tilt cause les défaites ». Les petits effectifs ne prouvent pas une tendance.')
        tags = [r['tag'] for r in groups]
        if st.session_state.get('journal_tag_selected') not in tags:
            st.session_state['journal_tag_selected'] = tags[0]
        tag = st.selectbox('Tag à examiner', tags, key='journal_tag_selected')
        row = next(r for r in groups if r['tag'] == tag)
        st.button(f'Voir les {row["n"]} matchs de ce tag →', key='journal_tag_sources',
                  on_click=open_match_sources, args=(owner, row['source_ids'], 'Tag manuel : ' + tag), kwargs={'origin': 'journal'})
