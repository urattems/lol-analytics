"""Readable local match history; filters delegate to the shared selection engine."""
from datetime import datetime, time

import streamlit as st

from analytics.explorer import ExplorerSelection
from analytics.evidence import EvidenceUnavailable, select_evidence
from analytics.constants import DEFAULT_INCLUDE_SHORT_GAMES
from core.db import Database
from core.queues import queue_name
from ui.components import empty_state, page_header, scope_bar
from ui.formatting import context_label
from ui.history_cards import render_history_card
from ui.history_navigation import cached_history_library
from ui.page_helpers import database_revision
from ui.profiles import get_active_player, get_active_settings
from ui.player_actions import identity_recovery_panel


def reset_history_filters():
    for key in list(st.session_state):
        if key.startswith("history_filter_"):
            del st.session_state[key]
    st.session_state["history_saved_widgets"] = {}
    st.session_state["history_offset"] = 0
    st.session_state["history_expanded_match"] = None


def _filters(library):
    games = library.games
    for key, value in st.session_state.get("history_saved_widgets", {}).items():
        if key not in st.session_state:
            st.session_state[key] = value
    columns = st.columns([1.2, 1.3, 1, 1.4, 1], vertical_alignment="bottom")
    windows = {"Tout l’historique": None, "20 dernières": 20, "50 dernières": 50, "100 dernières": 100, "500 dernières": 500}
    window = columns[0].selectbox("Fenêtre", list(windows), key="history_filter_window")
    def choose(area, label, column, formatter=None):
        options = [None, *sorted(games[column].drop_nulls().unique().to_list())]
        key = "history_filter_" + column
        if st.session_state.get(key) not in options:
            st.session_state.pop(key, None)
        return area.selectbox(label, options, format_func=lambda value: "Tous" if value is None else formatter(value) if formatter else str(value), key=key)
    champion = choose(columns[1], "Champion", "champion")
    role = choose(columns[2], "Rôle", "role", context_label)
    queue = choose(columns[3], "File", "queue_id", queue_name)
    result = columns[4].selectbox("Résultat", [None, True, False], format_func=lambda v: "Tous" if v is None else "WIN" if v else "LOSS", key="history_filter_win")
    with st.container(horizontal=True, vertical_alignment="center"):
        include = st.checkbox("Inclure parties <5 min", value=DEFAULT_INCLUDE_SHORT_GAMES, key="history_filter_include_short")
        with st.popover("Filtres avancés", icon=":material/tune:"):
            patch = choose(st, "Patch", "patch")
            timeline = st.selectbox("Timeline", ["all", "available", "missing"], format_func=lambda x: {"all": "Toutes", "available": "Disponible", "missing": "Absente"}[x], key="history_filter_timeline")
            for relation, label in (("teammate", "Avec un coéquipier"), ("opponent", "Contre un joueur")):
                camp = "ally" if relation == "teammate" else "enemy"
                ids = sorted({identifier for side, identifier in library.shared if side == camp}, key=lambda x: library.identities[x].local_label.casefold())
                key = f"history_filter_{relation}"
                if st.session_state.get(key) not in [None, *ids]:
                    st.session_state.pop(key, None)
                st.selectbox(label, [None, *ids], format_func=lambda value: "Tous" if value is None else library.identities[value].local_label, key=key)
            dated = games["game_creation"].drop_nulls()
            dates = ()
            if len(dated):
                dates = st.date_input("Période facultative", value=[], key="history_filter_dates")
        st.button("Réinitialiser", key="history_reset", on_click=reset_history_filters, icon=":material/restart_alt:", type="tertiary")
    start = end = None
    if isinstance(dates, tuple) and len(dates) == 2:
        start = max(0, int(datetime.combine(dates[0], time.min).timestamp() * 1000))
        end = max(0, int(datetime.combine(dates[1], time.max).timestamp() * 1000))
    filters = ExplorerSelection(base_game_limit=windows[window], champion=champion, role=role, queue_id=queue,
        win=result, patch=patch, include_short_games=include, timeline_requirement=timeline, date_from_ms=start, date_to_ms=end)
    values = {key: st.session_state[key] for key in st.session_state if key.startswith("history_filter_")}
    if values != st.session_state.get("history_saved_widgets", {}):
        st.session_state["history_offset"] = 0
        st.session_state["history_expanded_match"] = None
    st.session_state["history_saved_widgets"] = values
    base_count = min(games.height, windows[window]) if windows[window] else games.height
    return library.select(filters, values.get("history_filter_teammate"), values.get("history_filter_opponent")), base_count


def _page(offset):
    st.session_state["history_offset"] = offset
    st.session_state["history_expanded_match"] = None


def show_history():
    settings = get_active_settings()
    database = Database(settings.database_path)
    player = get_active_player(database, settings)
    page_header("Vos parties. Vos repères.", "Les champions, les builds et les joueurs. Retrouvez une partie, puis remontez son histoire.", "HISTORIQUE")
    if not player:
        empty_state("Vos premières parties vous attendent", "Synchronisez ce profil depuis la bibliothèque, ou explorez la démo synthétique.")
        return
    library = cached_history_library(str(database.path), str(player["puuid"]), database_revision(database.path))
    if flash := st.session_state.pop("identity_flash", None):
        st.success(flash)
    if library.games.is_empty() and 'history_evidence' not in st.session_state:
        empty_state("Ce profil n’a pas encore de parties locales", "Importez son historique récent depuis Profils. Ouvrir cette page ne déclenche aucun appel Riot.")
        return
    if not library.games.is_empty():
        identity_recovery_panel(settings, library.owner, library.identities, compact=True)
    if st.session_state.get("history_social_back"):
        from ui.history_navigation import restore_history_selection
        st.button("← Sélection précédente", key="history_social_return", on_click=restore_history_selection, type="tertiary")
    evidence = st.session_state.get('history_evidence')
    if evidence is not None:
        from ui.history_navigation import evidence_navigation
        evidence_navigation()
        try:
            selected = select_evidence(library.games, library.owner, evidence)
        except EvidenceUnavailable as error:
            st.warning(str(error))
            return
        base_count = selected.height
        from analytics.exports import markdown_text
        st.info(f"{selected.height} matchs sources · {markdown_text(str(evidence.get('label', 'Statistique Champion')))}")
        st.caption('Sélection exacte de la statistique ; les filtres habituels de l’historique ne s’appliquent pas ici.')
    else:
        selected, base_count = _filters(library)
    for relation, label in (() if evidence is not None else (("teammate", "Avec"), ("opponent", "Contre"))):
        identifier = st.session_state.get("history_filter_" + relation)
        if identifier in library.identities:
            from analytics.exports import markdown_text
            st.caption(f"{label} {markdown_text(library.identities[identifier].local_label)} · rencontres dans la sélection")
    if selected.is_empty():
        empty_state("Aucune partie dans cette sélection", "Élargissez la fenêtre, retirez un filtre ou incluez les parties courtes. Rien n’a été retiré de la bibliothèque.")
        return
    offset = min(st.session_state.get("history_offset", 0), ((selected.height - 1) // 20) * 20)
    cards = library.batch(selected, offset)
    from core.ranks import load_rank_contexts
    rank_contexts = load_rank_contexts(database, library.owner, [card.match_id for card in cards])
    scope_bar(base_count, selected.height, f"Parties {offset + 1}–{offset + len(cards)} · 20 cartes maximum par page")
    for card in cards:
        render_history_card(card, library, settings, rank_contexts.get(card.match_id))
    with st.container(horizontal=True, horizontal_alignment="center"):
        st.button("← 20 précédentes", key="history_previous", disabled=offset == 0, on_click=_page, args=(max(0, offset - 20),))
        st.caption(f"Page {offset // 20 + 1} / {(selected.height + 19) // 20}")
        st.button("20 suivantes →", key="history_next", disabled=offset + 20 >= selected.height, on_click=_page, args=(offset + 20,))


if __name__ == '__main__':
    # A cold deep link can discover pages/ before the native router first runs.
    # Register the shared shell; regular imports only expose the page callable.
    from app import main
    main()
