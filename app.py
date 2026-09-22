"""Streamlit entry point for the complete local LoL Analytics V2."""

from __future__ import annotations

import sqlite3
import streamlit as st

from config.settings import PROJECT_ROOT, get_settings
from config.version import APP_VERSION
from core.db import Database
from core.diagnostics import safe_message
from ui.errors import show_diagnostic
from pages.ai_export import show_ai_export
from pages.build_lab import show_build_lab
from pages.champions import show_champions
from pages.contexts import show_contexts
from pages.insights import show_insights
from pages.explorer import show_explorer
from pages.overview import show_overview
from pages.history import show_history
from pages.session_review import show_session_review
from pages.timeline import show_timeline
from pages.profiles import show_profiles
from pages.progression import show_progression
from pages.journal import show_journal
from ui.profiles import render_profile_selector, active_profile_banner, get_active_player
from ui.library import render_library_controls
from ui.theme import APP_TITLE, apply_theme


def main() -> None:
    """Keep expected SQLite failures out of Streamlit tracebacks on every page."""
    from ui.errors import configure_error_display
    configure_error_display()
    try:
        _render_app()
    except sqlite3.DatabaseError as error:
        st.error("La base locale est indisponible ou verrouillée. " + safe_message(error) + " Aucune réinitialisation automatique n’a été effectuée.")
        show_diagnostic(error, phase='render')


def _render_app() -> None:
    """Render the shared shell and profile-aware native navigation."""

    st.set_page_config(page_title=APP_TITLE, page_icon="🎮", layout="wide")
    apply_theme()
    from core.runtime import ensure_plot_runtime
    try:
        ensure_plot_runtime()
    except Exception as error:
        st.warning("L’environnement graphique est indisponible. Consultez les logs ou lancez python -m scripts.check_environment. Les données locales ne sont pas réinitialisées.")
        show_diagnostic(error, phase='startup')
    settings = get_settings()
    database = Database(settings.database_path)

    try:
        database.initialize()
    except Exception as error:
        st.error("Impossible d'ouvrir la base locale. " + safe_message(error))
        show_diagnostic(error, phase='startup')
        return

    st.logo(str(PROJECT_ROOT / "ui" / "assets" / "logo.svg"), size="large")
    selected_page = st.navigation(
        {"VOTRE JEU": [
            st.Page(show_overview, title="Accueil", icon=":material/space_dashboard:", url_path="overview", default=True),
            st.Page(show_champions, title="Champions", icon=":material/shield:", url_path="champions"),
            st.Page(show_progression, title="Progression", icon=":material/trending_up:", url_path="progression"),
            st.Page(show_journal, title="Journal", icon=":material/book:", url_path="journal"),
        ], "PARTIES": [
            st.Page(show_history, title="Historique", icon=":material/history:", url_path="history"),
            st.Page(show_session_review, title="Sessions", icon=":material/view_carousel:", url_path="session-review"),
        ], "OUTILS": [
            st.Page(show_explorer, title="Explorer", icon=":material/tune:", url_path="explorer"),
            st.Page(show_ai_export, title="Export IA", icon=":material/package_2:", url_path="ai-export"),
            st.Page(show_insights, title="Insights", url_path="insights", visibility="hidden"),
            st.Page(show_contexts, title="Contexts", url_path="contexts", visibility="hidden"),
            st.Page(show_build_lab, title="Build Lab", url_path="build-lab", visibility="hidden"),
            st.Page(show_timeline, title="Timeline", url_path="timeline", visibility="hidden"),
        ], "BIBLIOTHÈQUE": [
            st.Page(show_profiles, title="Profils", icon=":material/group:", url_path="profiles"),
        ]},
        position="sidebar",
        expanded=True,
    )
    settings = render_profile_selector(settings, database)
    with st.sidebar.expander("Analyses complémentaires"):
        for function, title, path in (
            (show_contexts, "Contextes", "contexts"), (show_insights, "Insights", "insights"),
            (show_build_lab, "Build Lab · inventaires finaux", "build-lab"), (show_timeline, "Timeline", "timeline"),
        ):
            st.page_link(st.Page(function, title=title, url_path=path), label=title)
    player = get_active_player(database, settings)
    player_puuid = str(player["puuid"]) if player else None
    local_count = database.count_player_matches(player_puuid) if player_puuid else 0
    render_library_controls(settings, database, local_count)
    st.sidebar.caption(
        f"LoL Analytics · v{APP_VERSION}  \nApplication locale · non affiliée à Riot Games"
    )
    active_profile_banner(settings)

    flash = st.session_state.pop("library_flash", None)
    if flash:
        st.success(flash)

    review_cta = st.session_state.get("review_post_sync")
    if review_cta and review_cta.get("owner") == player_puuid:
        cta, dismiss = st.columns([3, 1])
        if cta.button("Bibliothèque actualisée · Revoir la dernière session →", key="review_after_sync", width="stretch"):
            from ui.session_navigation import open_session_review
            st.session_state.pop("review_post_sync", None)
            open_session_review(review_cta.get("anchor"))
        if dismiss.button("Masquer", key="review_dismiss_sync"):
            st.session_state.pop("review_post_sync", None)
            st.rerun()

    selected_page.run()
    from ui.player_actions import render_player_dialog
    render_player_dialog(settings, player_puuid)


if __name__ == "__main__":
    main()
