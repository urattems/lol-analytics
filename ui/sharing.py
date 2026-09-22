"""Prepare locally, inspect the exact sanitized payload, then opt into download."""
import json

import streamlit as st

from analytics.share import ShareOptions, prepare_share, share_zip
from ui.page_helpers import database_revision
from ui.journal import journal_error


def render_sharing(database, owner, games):
    st.caption('Format de revue distinct du bundle IA historique. Aucun envoi automatique. Les matchs et joueurs reçoivent des alias propres à cet export ; le contexte peut encore permettre un recoupement.')
    st.warning('Le texte libre peut identifier quelqu’un même après retrait des noms connus. N’incluez pas de secret ni d’information personnelle ; relisez l’aperçu avant de partager.')
    left, right = st.columns(2)
    performance = left.checkbox('Statistiques finales', value=True, key='journal_share_performance')
    checkpoints = left.checkbox('Checkpoints personnels', key='journal_share_checkpoints')
    rosters = right.checkbox('Compositions avec alias de joueurs', key='journal_share_rosters')
    dates = right.checkbox('Dates exactes des parties · plus facilement recoupables', key='journal_share_dates')
    annotations = st.checkbox('Proposer mes notes et tags dans l’aperçu', key='journal_share_annotations')
    options = ShareOptions(performance, checkpoints, rosters, dates, annotations)
    signature = (str(database.path.resolve()), owner, tuple(games['match_id']), options, database_revision(database.path))
    prepared = st.session_state.get('journal_share_prepared')
    if prepared and prepared['signature'] != signature:
        st.session_state.pop('journal_share_prepared', None)
        st.session_state.pop('journal_share_consent', None)
        st.info('La sélection, les options ou la bibliothèque ont changé. Préparez un nouvel aperçu.')
        prepared = None
    if st.button('Préparer l’aperçu expurgé', key='journal_share_prepare', disabled=games.height > 2500):
        try:
            preview = prepare_share(database, owner, games['match_id'].to_list(), options)
        except Exception as error:
            journal_error(error)
        else:
            # Read transaction is coherent. If the library changed in parallel,
            # don't label this preview as the current revision of its UI scope.
            if signature[-1] != database_revision(database.path):
                st.warning('La bibliothèque a changé pendant la préparation. Rechargez le journal et réessayez.')
            else:
                st.session_state['journal_share_prepared'] = {'signature': signature, 'preview': preview}
                st.session_state.pop('journal_share_consent', None)
                st.rerun()
    if games.height > 2500:
        st.info('Ce format de partage est limité à 2 500 parties. Réduisez la fenêtre ; les autres exports restent disponibles.')
    if not prepared:
        return
    preview = prepared['preview']
    rows, notes = json.loads(preview.matches_json), json.loads(preview.annotations_json)
    st.success(f'Aperçu prêt : {len(rows)} parties · {len(notes)} annotations · {preview.redacted_values} valeurs expurgées.')
    with st.expander('Aperçu exact des champs partagés', expanded=True):
        st.json({'matches': rows, 'annotations': notes}, expanded=False)
    consent = False
    if annotations:
        st.caption('Annotations après expurgation — c’est ce texte, et non votre note privée originale, qui sera inclus :')
        for note in notes:
            with st.expander(note['match_ref']):
                st.text(note['note'])
                st.text(', '.join(note['tags']))
        consent = st.checkbox('J’ai relu les annotations expurgées et je confirme leur inclusion dans le ZIP', key='journal_share_consent')
    if annotations and not consent:
        st.info('Le téléchargement avec annotations nécessite votre confirmation explicite.')
        return
    content = share_zip(preview, annotations_reviewed=consent)
    st.download_button('Télécharger la revue expurgée', content, file_name='lol-analytics-revue.zip', mime='application/zip', key='journal_share_download')
    st.caption('4 fichiers fixes, ou 5 avec annotations : manifest.json, report.md, matches.jsonl, matches.csv, annotations.jsonl optionnel. Aucun identifiant de match original, PUUID ou nom de joueur n’est volontairement projeté dans ce format.')
