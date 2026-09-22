"""Profile-scoped History navigation and cached batched sources."""
import streamlit as st

from analytics.history import prepare_history
from core.db import Database
from ui.page_helpers import cached_context_dataset


@st.cache_data(show_spinner=False, max_entries=12)
def cached_history_library(path: str, puuid: str, revision: int):
    database = Database(path)
    return prepare_history(cached_context_dataset(path, puuid, revision),
                           database.participants_for_player_matches(puuid), puuid, database.list_players())


@st.cache_data(show_spinner=False, max_entries=40)
def cached_history_item_events(path: str, owner: str, match_id: str, revision: int):
    return Database(path).history_item_events(owner, match_id)


def open_history(teammate: str | None = None, opponent: str | None = None, *, reset=False):
    if reset or teammate or opponent:
        st.session_state.pop("history_evidence", None)
    if not reset and (teammate or opponent) and st.session_state.get("history_saved_widgets"):
        st.session_state["history_social_back"] = {
            "widgets": dict(st.session_state["history_saved_widgets"]),
            "offset": st.session_state.get("history_offset", 0),
            "expanded": st.session_state.get("history_expanded_match"),
        }
    elif reset:
        st.session_state.pop("history_social_back", None)
    if reset or teammate or opponent:
        for key in list(st.session_state):
            if key.startswith("history_filter_"):
                del st.session_state[key]
        st.session_state["history_saved_widgets"] = {}
        st.session_state["history_offset"] = 0
        st.session_state["history_expanded_match"] = None
        st.session_state["history_filter_teammate"] = teammate
        st.session_state["history_filter_opponent"] = opponent
    st.query_params.clear()
    from pages.history import show_history
    st.switch_page(st.Page(show_history, title="Historique", url_path="history"))


def restore_history_selection():
    previous = st.session_state.pop("history_social_back", None)
    if not previous:
        return
    for key in list(st.session_state):
        if key.startswith("history_filter_"):
            del st.session_state[key]
    st.session_state.update(previous["widgets"])
    st.session_state["history_saved_widgets"] = previous["widgets"]
    st.session_state["history_offset"] = previous["offset"]
    st.session_state["history_expanded_match"] = previous["expanded"]


def open_history_timeline(match_id: str, owner: str):
    st.session_state.pop("review_timeline_context", None)
    st.session_state["history_timeline_context"] = {"owner": owner, "match": match_id}
    st.session_state["timeline_requested_match"] = match_id
    st.query_params.clear()
    from pages.timeline import show_timeline
    st.switch_page(st.Page(show_timeline, title="Timeline", url_path="timeline"))


def timeline_history_navigation(database: Database, owner: str):
    context = st.session_state.get("history_timeline_context")
    if not context:
        return None
    requested = st.session_state.get("timeline_requested_match") or st.session_state.get("timeline_selected_match") or context.get("match")
    own_ids = {row["match_id"] for row in database.player_matches(owner)}
    if context.get("owner") != owner or context.get("match") not in own_ids or requested != context.get("match"):
        st.session_state.pop("history_timeline_context", None)
        return None
    if st.button("← Retour à l’historique", key="history_back"):
        st.session_state["history_expanded_match"] = context["match"]
        open_history()
    return context["match"]


def open_champion_sources(owner: str, match_ids, label: str):
    open_match_sources(owner, match_ids, label, origin='champions')


def open_match_sources(owner: str, match_ids, label: str, *, origin='champions'):
    """Persist a cohort, not filters that might silently widen it on another page."""
    if origin not in {'champions', 'progression', 'overview', 'journal'}:
        raise ValueError('Destination de retour non prise en charge.')
    st.session_state['history_evidence'] = {'owner': owner, 'match_ids': tuple(match_ids), 'label': label, 'origin': origin}
    st.session_state['history_offset'] = 0
    st.session_state['history_expanded_match'] = None
    st.session_state.pop('history_social_back', None)
    open_history()


def evidence_navigation():
    """History -> Timeline -> History keeps the cohort; returning restores the champion widgets."""
    context = st.session_state.get('history_evidence')
    origin = context.get('origin', 'champions') if isinstance(context, dict) else 'champions'
    labels = {'champions': 'la fiche Champion', 'progression': 'Progression', 'overview': 'l’accueil', 'journal': 'Journal'}
    origin = origin if origin in labels else 'champions'
    if st.button('← Retour à ' + labels[origin], key='history_evidence_back'):
        st.session_state.pop('history_evidence', None)
        from pages.champions import show_champions
        from pages.progression import show_progression
        from pages.overview import show_overview
        from pages.journal import show_journal
        st.query_params.clear()
        if origin == 'overview':
            st.switch_page(st.Page(show_overview, title='Accueil', url_path='overview', default=True))
        else:
            function = {'champions': show_champions, 'progression': show_progression, 'journal': show_journal}[origin]
            st.switch_page(st.Page(function, title=labels[origin], url_path=origin))
    if st.button('Quitter les matchs sources', key='history_evidence_exit', type='tertiary'):
        st.session_state.pop('history_evidence', None)
        st.session_state['history_offset'] = 0
        st.rerun()
