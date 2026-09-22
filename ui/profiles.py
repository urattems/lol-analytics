"""One explicit active profile per browser session, independent of page filters."""
from __future__ import annotations

from html import escape
from collections.abc import MutableMapping

import streamlit as st

from config.settings import Settings, get_settings
from core.db import Database
from core.profiles import settings_for_profile


PROFILE_WIDGET_PREFIXES = (
    "overview_", "champion", "insights_", "contexts_", "_contexts_", "build_",
    "timeline_", "explorer_", "export_", "ai_", "library_", "review_", "history_", "identity_", "progression_", "journal_",
)


def clear_profile_state(state: MutableMapping, puuid: str | None) -> None:
    """A saved Explorer selection must never be carried to a different player."""
    for key in list(state):
        if str(key).startswith(PROFILE_WIDGET_PREFIXES):
            del state[key]
    state["active_profile_puuid"] = puuid


def activate_profile(puuid: str | None) -> None:
    clear_profile_state(st.session_state, puuid)
    st.query_params.clear()


def get_active_settings() -> Settings:
    base = get_settings()
    scope = (str(base.database_path.resolve()), base.riot_game_name, base.riot_tag_line)
    if st.session_state.get("profile_database_scope") != scope:
        clear_profile_state(st.session_state, None)
        st.session_state["profile_database_scope"] = scope
    puuid = st.session_state.get("active_profile_puuid")
    if puuid:
        profile = Database(base.database_path).get_player(str(puuid))
        if profile:
            return settings_for_profile(base, profile)
        clear_profile_state(st.session_state, None)
    return base


def get_active_player(database: Database, settings: Settings) -> dict[str, object] | None:
    """Use the selected stable identity even if an old Riot ID was reassigned."""
    puuid = st.session_state.get("active_profile_puuid")
    if puuid:
        return database.get_player(str(puuid))
    return database.find_player(settings.riot_game_name, settings.riot_tag_line)


def render_profile_selector(base: Settings, database: Database) -> Settings:
    active = get_active_settings()
    primary = database.find_player(base.riot_game_name, base.riot_tag_line)
    primary_id = str(primary["puuid"]) if primary else None
    profiles = [p for p in database.list_players() if p["puuid"] != primary_id]
    labels = {"main": f"{base.riot_id} · principal"}
    labels.update({str(p["puuid"]): f'{p["game_name"]}#{p["tag_line"]}' for p in profiles})
    current = st.session_state.get("active_profile_puuid")
    value = str(current) if current and current != primary_id and str(current) in labels else "main"
    # A profile card can change the durable identity after this widget rendered.
    # Reconcile on the next run so its old browser value cannot select main again.
    st.session_state["profile_selector"] = value
    st.sidebar.selectbox(
        "Profil analysé", list(labels), format_func=labels.get,
        key="profile_selector", on_change=_select_profile,
    )
    if value != "main":
        st.sidebar.button("↩ Mon profil principal", on_click=activate_profile, args=(None,), width="stretch")
    if st.sidebar.button("Chercher un profil", icon=":material/person_search:", width="stretch"):
        from pages.profiles import show_profiles
        st.switch_page(st.Page(show_profiles, title="Profils", url_path="profiles"))
    return active


def _select_profile() -> None:
    selected = st.session_state.get("profile_selector", "main")
    activate_profile(None if selected == "main" else str(selected))


def active_profile_banner(settings: Settings) -> None:
    primary = get_settings()
    is_main = (settings.riot_game_name, settings.riot_tag_line) == (primary.riot_game_name, primary.riot_tag_line)
    st.markdown(
        f'<div class="la-profile-chip"><b>{escape(settings.riot_id)}</b> · '
        f'{escape(settings.riot_platform_region)} · {"Profil principal" if is_main else "Profil consulté"}'
        f'{" · Démonstration synthétique" if settings.demo_mode else ""}</div>', unsafe_allow_html=True,
    )
