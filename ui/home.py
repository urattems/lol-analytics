"""Three evidence-backed home actions; existing detailed analytics stay secondary."""
import polars as pl
import streamlit as st

from analytics.exports import markdown_text
from analytics.overview import get_overview_summary
from analytics.progression import METRICS, home_observation, review_candidate, window_context
from ui.components import recent_form_strip
from ui.formatting import decimal_label, match_date_label, playtime_label
from ui.progression import scope_label, value_label
from ui.history_navigation import open_match_sources


def render_home_actions(database, settings, owner, all_games, selected, include_short):
    session_col, observation_col, coverage_col = st.columns([1, 1.2, 1])
    with session_col, st.container(border=True):
        st.markdown('#### Reprendre sa dernière session')
        dated = all_games.filter(pl.col('session_id').is_not_null()).sort(['game_creation', 'match_id'], descending=[True, True])
        latest = dated.row(0, named=True) if not dated.is_empty() else None
        session = all_games.filter(pl.col('session_id') == latest['session_id']) if latest else all_games.head(0)
        analytical = session if include_short else session.filter(pl.col('duration').is_null() | (pl.col('duration') >= 300))
        summary = get_overview_summary(analytical)
        if latest:
            st.markdown(f'**{summary["wins"]} V / {summary["losses"]} D** · {analytical.height} parties analysées')
        if st.button('Revoir la dernière session →', key='overview_review', width='stretch', disabled=not latest):
            from ui.session_navigation import open_session_review
            open_session_review(str(latest['match_id']))
        if latest:
            st.caption(match_date_label(latest['game_creation'], True))
            recent_form_strip(analytical)
            st.caption(f'{session.height} parties dans la session · pause ≤45 min · {playtime_label(int(analytical["duration"].sum()))} en jeu')
            if analytical.height != session.height:
                st.caption(f'{session.height - analytical.height} parties courtes conservées, exclues des indicateurs.')
        else:
            st.info('Aucune session datée exploitable. Les parties sans date restent dans l’historique.')
    with observation_col, st.container(border=True):
        observation = home_observation(all_games, include_short=include_short)
        if observation:
            key = observation['key']
            comparison = observation['comparison']
            st.markdown('#### Un écart récent à examiner')
            st.markdown(f'**{METRICS[key].label} : {value_label(observation["previous"]["value"], key)} → '
                        f'{value_label(observation["recent"]["value"], key)}**')
            st.caption(scope_label(comparison.scope, comparison.effective_patch))
            st.caption(f'N exploitable récent {observation["recent"]["n"]} / précédent {observation["previous"]["n"]} · historique local, deux fenêtres disjointes')
            ids = (*observation['recent']['source_ids'], *observation['previous']['source_ids'])
            if st.button(f'Examiner les {len(ids)} matchs sources →', key='overview_observation', width='stretch'):
                open_match_sources(owner, ids, METRICS[key].label + ' · récent / précédent · ' +
                                   scope_label(comparison.scope, comparison.effective_patch), origin='overview')
            for frame, label in ((comparison.previous, 'Précédent'), (comparison.recent, 'Récent')):
                period = window_context(frame)
                st.caption(f'{label} : {match_date_label(period["first"])} → {match_date_label(period["last"])}')
            st.caption('Variation descriptive, pas une preuve de progression ni une explication des victoires.')
        else:
            candidate = review_candidate(selected)
            st.markdown('#### Une partie à revoir' if candidate else '#### Construire ses repères')
            if candidate:
                st.markdown(f'**{markdown_text(candidate["champion"])} · défaite**')
                st.caption(f'Avance personnelle @15 : {decimal_label(candidate["gold_diff_15"], 0)} or face à l’adversaire du même rôle déclaré.')
                st.caption('N=1 · cas de revue, pas une tendance ni une cause identifiée. Partie la plus récente répondant à ce critère dans la sélection.')
                if st.button('Revoir cette partie →', key='overview_candidate', width='stretch'):
                    open_match_sources(owner, [candidate['match_id']], 'Défaite avec avance personnelle ≥1 000 or @15', origin='overview')
            else:
                st.info('Pas encore deux échantillons exploitables d’au moins 10 parties sur le même patch pour afficher un constat récent.')
                st.caption('La page Progression affiche aussi les petits effectifs, avec leurs limites et les dates réellement disponibles.')
    with coverage_col, st.container(border=True):
        st.markdown('#### Compléter ses données')
        missing = selected.filter(pl.col('timeline_available').fill_null(False) == False)  # noqa: E712
        st.markdown(f'**{selected.height - missing.height} / {selected.height} Timelines**')
        st.caption('Dans la fenêtre choisie sur l’accueil. Les métriques manquantes restent indisponibles, jamais égales à zéro.')
        if missing.height:
            st.caption('Ouvrez une partie manquante puis sa Timeline pour lancer son enrichissement explicitement.')
            if st.button(f'Voir les {missing.height} parties à compléter →', key='overview_missing', width='stretch'):
                open_match_sources(owner, missing['match_id'].to_list(), 'Timelines manquantes dans la sélection de l’accueil', origin='overview')
        else:
            st.caption('Toutes les Timelines sont présentes dans cette fenêtre. Leur présence ne garantit pas la qualité de chaque événement ou checkpoint.')
