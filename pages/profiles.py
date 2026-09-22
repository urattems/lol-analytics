"""Explicit Riot ID lookup and local profile library."""
from __future__ import annotations

from analytics.exports import markdown_text

import streamlit as st

from config.settings import get_settings
from core.db import Database
from core.diagnostics import record_failure, safe_message
from core.import_lock import import_is_running, ImportBusyError
from core.profiles import PLATFORM_ROUTING_REGIONS, import_profile, settings_for_lookup, remove_profile_from_library
from ui.components import empty_state, page_header, stat_strip
from ui.formatting import sync_date_label, match_date_label
from ui.library import _riot_error_message
from ui.profiles import activate_profile

def show_profiles() -> None:
    base = get_settings()
    database = Database(base.database_path)
    busy = import_is_running(database.path)
    page_header("Une autre perspective.", "Retrouvez les parties d’un mate et explorez son historique avec les mêmes outils.", "PROFILS · RIOT ID")
    st.markdown('<div class="la-export-card"><div class="la-eyebrow">RECHERCHE DE JOUEUR</div><div class="la-title">À qui le tour ?</div><div class="la-meta">Un Riot ID, un serveur, et ses dernières parties. Le profil principal reste à portée de clic.</div></div>', unsafe_allow_html=True)
    with st.form("profile_lookup", border=False):
        name_col, server_col, count_col = st.columns([2.6, 1, 1])
        riot_id = name_col.text_input("Riot ID", placeholder="Pseudo#TAG", max_chars=81)
        platforms = list(PLATFORM_ROUTING_REGIONS)
        platform = server_col.selectbox("Serveur", platforms, index=platforms.index(base.riot_platform_region) if base.riot_platform_region in platforms else 0)
        count = count_col.selectbox("Parties récentes", [20, 50])
        submitted = st.form_submit_button("Rechercher et importer", type="primary", icon=":material/search:", disabled=base.demo_mode or busy)
    st.caption("La recherche importe les dernières parties une seule fois. Les profils déjà consultés restent disponibles localement.")
    if base.demo_mode:
        st.info("Mode démo : profils inventés, appels Riot désactivés.")
    if submitted:
        try:
            settings_for_lookup(base, riot_id, platform)
            progress = st.progress(0, text="Recherche du joueur…")
            def update(event):
                fraction = event.current / event.total if event.total else 0
                progress.progress(min(1.0, fraction), text=f"Import des parties · {event.current} / {event.total or count}")
            result = import_profile(base, riot_id, platform, count, update)
            st.cache_data.clear()
            activate_profile(result.account.puuid)
            progress.progress(1.0, text="Profil prêt à analyser")
            st.session_state["library_flash"] = f"{result.account.game_name}#{result.account.tag_line} : {result.inserted} nouvelle(s) partie(s) importée(s)."
            _open_overview()
        except ValueError as error:
            st.error(str(error))
        except Exception as error:
            record_failure(error, phase='profiles')
            st.error(_riot_error_message(error))

    st.subheader("Votre bibliothèque de profils")
    if flash := st.session_state.pop("profile_removed_flash", None):
        st.success(flash)
    profiles = database.list_players()
    if not profiles:
        empty_state("Le premier profil vous attend", "Synchronisez votre compte principal depuis la bibliothèque, ou recherchez un Riot ID ci-dessus.")
        return
    primary = database.find_player(base.riot_game_name, base.riot_tag_line)
    primary_id = primary["puuid"] if primary else None
    profiles.sort(key=lambda p: (p["puuid"] != primary_id, str(p["game_name"]).casefold()))
    for offset in range(0, len(profiles), 2):
        for column, profile in zip(st.columns(2), profiles[offset:offset + 2]):
            with column.container(border=True):
                is_primary = profile["puuid"] == primary_id
                st.caption("PROFIL PRINCIPAL" if is_primary else "PROFIL SECONDAIRE")
                st.markdown(f'### {markdown_text(profile["game_name"])}#{markdown_text(profile["tag_line"])}')
                stat_strip([("Parties locales", str(profile["local_games"])), ("Serveur", str(profile["platform_region"] or base.riot_platform_region).upper())])
                st.caption(f'Dernière partie locale : {match_date_label(profile["last_activity_at"])}')
                st.caption(f'Dernière sync : {sync_date_label(profile["last_sync_at"])}')
                if not is_primary:
                    with st.popover("Options du profil", icon=":material/more_horiz:"):
                        if st.button("Supprimer le profil et ses données exclusives", key=f'profile_remove_{profile["puuid"]}',
                                     disabled=primary_id is None or busy):
                            st.session_state["profile_remove_request"] = str(profile["puuid"])
                            st.rerun()
                        st.caption("Les parties partagées avec un autre profil restent conservées.")
                if st.button(
                    "Analyser ce profil", key=f'profile_open_{profile["puuid"]}', width="stretch",
                    on_click=activate_profile, args=(None if is_primary else str(profile["puuid"]),),
                ):
                    _open_overview()
    if not any(p["puuid"] != primary_id for p in profiles):
        st.caption("Aucun profil secondaire. Recherchez un mate ci-dessus quand vous le souhaitez.")
    if request := st.session_state.get("profile_remove_request"):
        _confirm_removal(base, request)


def _dismiss_removal():
    st.session_state.pop("profile_remove_request", None)


@st.dialog("Supprimer ce profil et ses données locales ?", on_dismiss=_dismiss_removal)
def _confirm_removal(base, puuid):
    profile = Database(base.database_path).get_player(puuid)
    if profile is None:
        _dismiss_removal()
        st.rerun()
    st.markdown(f'**{markdown_text(profile["game_name"])}#{markdown_text(profile["tag_line"])}**')
    counts = Database(base.database_path).profile_removal_counts(puuid)
    st.warning("Les matchs uniquement associés à ce profil seront supprimés, avec leurs participants, Timelines, frames et événements. Les matchs également utilisés par un autre profil enregistré resteront conservés. Ce nettoyage local n’est pas annulable depuis l’application.")
    st.info('Une sauvegarde SQLite complète et vérifiée sera créée dans le dossier backups à côté de votre base avant toute suppression. Si elle échoue, rien ne sera supprimé. Elle contient des données personnelles : ne la partagez pas.')
    st.caption(f'Prévision : {counts["exclusive"]} partie(s) exclusive(s) à supprimer · {counts["shared"]} partie(s) partagée(s) à conserver. Le calcul est vérifié à nouveau lors de la confirmation.')
    cancel, confirm = st.columns(2)
    if cancel.button("Annuler", key="profile_remove_cancel", width="stretch"):
        _dismiss_removal()
        st.rerun()
    if confirm.button("Supprimer", key="profile_remove_confirm", type="primary", width="stretch"):
        try:
            # Switch BEFORE deleting: no subsequent render can target an orphan.
            if st.session_state.get("active_profile_puuid") == puuid:
                activate_profile(None)
            removed = remove_profile_from_library(base, puuid)
        except ImportBusyError as error:
            st.error(str(error))
        except Exception as error:
            record_failure(error, phase='removal')
            st.error('Retrait impossible. ' + safe_message(error) + ' Aucune partie n’a été supprimée.')
        else:
            _dismiss_removal()
            st.cache_data.clear()
            st.session_state["profile_removed_flash"] = (
                f"Profil supprimé : {removed.deleted_matches} partie(s) exclusive(s) supprimée(s), "
                f"{removed.shared_matches} partie(s) partagées conservées."
                + (f' Sauvegarde vérifiée : backups/{removed.backup_path.name} · {removed.backup_created_at}.' if removed.backup_path else '')
            ) if removed else "Ce profil avait déjà été supprimé."
            st.rerun()


def _open_overview() -> None:
    from pages.overview import show_overview
    st.switch_page(st.Page(show_overview, title="Overview", url_path="overview", default=True))


if __name__ == '__main__':
    # A cold deep link can discover pages/ before the native router first runs.
    # Register the shared shell; regular imports only expose the page callable.
    from app import main
    main()
