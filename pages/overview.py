"""Post-session home: recent form, one session, and directly reviewable games."""
from __future__ import annotations

from html import escape
import polars as pl
import streamlit as st

from analytics.discovery import promoted_insights
from analytics.overview import champion_summary, get_overview_summary
from analytics.constants import DEFAULT_INCLUDE_SHORT_GAMES, SHORT_GAME_THRESHOLD_SECONDS
from core.db import Database
from core.static_data import get_static_data_service
from ui.components import discovery_card, empty_state, match_history_cards, page_header, render_kpis, render_recent_form, section_header
from ui.formatting import decimal_label, sync_date_label
from ui.page_helpers import cached_context_dataset, database_revision, load_selected_dataset
from ui.profiles import get_active_settings, get_active_player


def show_overview() -> None:
    settings = get_active_settings()
    database = Database(settings.database_path)
    static_data = get_static_data_service()
    player = get_active_player(database, settings)
    if not player:
        page_header("Tout commence par une partie.", "Votre historique, vos champions et vos sessions prennent vie ici.", "BIENVENUE")
        empty_state("Importez vos premières parties", "Cliquez sur Synchroniser dans la bibliothèque à gauche. Pour consulter un mate, utilisez Chercher un profil et son Riot ID.")
        _profile_action()
        return
    puuid = str(player["puuid"])
    all_context = cached_context_dataset(str(settings.database_path), puuid, database_revision(settings.database_path))
    if all_context.is_empty():
        empty_state("Ce profil est prêt à être exploré", "Aucune partie locale pour ce joueur. Lancez une synchronisation ou vérifiez le serveur sélectionné dans Profils.")
        _profile_action()
        return
    include_short = st.session_state.get("overview_include_short_games", DEFAULT_INCLUDE_SHORT_GAMES)
    analytical_library = all_context if include_short else all_context.filter(pl.col("duration").is_null() | (pl.col("duration") >= SHORT_GAME_THRESHOLD_SECONDS))
    top = champion_summary(analytical_library).row(0, named=True) if not analytical_library.is_empty() else {"champion": all_context["champion"][0], "games": 0, "winrate": None}
    asset = static_data.champion(str(top["champion"]))
    background = f"linear-gradient(90deg,rgba(11,16,24,.97) 10%,rgba(11,16,24,.68) 70%,rgba(11,16,24,.40)),url('{escape(asset.splash_url)}')" if asset.splash_url else "linear-gradient(115deg,#192433,#101722)"
    st.markdown(
        f'<section class="la-profile-hero la-home-hero" style="background-image:{background}"><div>'
        f'<div class="la-eyebrow">LE JEU. EN PERSPECTIVE.</div><h1>{escape(settings.riot_game_name)}<span style="color:#9aaec2;font-size:.45em"> #{escape(settings.riot_tag_line)}</span></h1>'
        f'<p>{all_context.height} parties locales · dernière sync {sync_date_label(database.last_sync_at(puuid))}</p></div>'
        f'<div class="la-hero-aside"><small>CHAMPION LE PLUS JOUÉ · HISTORIQUE LOCAL</small><strong>{escape(asset.display_name)}</strong>'
        f'<p>{top["games"]} parties · {decimal_label(top["winrate"],1," %")} WR</p></div></section>', unsafe_allow_html=True,
    )
    games, _ = load_selected_dataset(database, settings.database_path, puuid, "overview")
    if games.is_empty():
        empty_state("Aucune partie dans cette fenêtre", "Élargissez la sélection ou réactivez les parties de moins de cinq minutes.")
        return
    context = all_context.filter(pl.col("match_id").is_in(games["match_id"].to_list()))
    from ui.home import render_home_actions
    from core.profile_ranks import load_profile_ranks
    from core.ranks import rank_label
    from ui.profile_ranks import rank_date
    ranks = load_profile_ranks(database, puuid, settings.riot_platform_region, 420)
    if ranks:
        observed = ranks[-1]
        st.caption(f"Solo/Duo observé : {rank_label(observed)} · {rank_date(observed['observed_at'])} · pas un rang historique reconstitué")
    render_home_actions(database, settings, puuid, all_context, context, include_short)
    with st.expander('Forme récente, statistiques et dernières parties'):
        summary = get_overview_summary(games)
        render_kpis(summary)
        st.caption(f'{summary["wins"]} victoires · {summary["losses"]} défaites dans la sélection')
        render_recent_form(games)
        section_header('Vos dernières parties', 'Les plus récentes de la sélection · Revoir ouvre leur Timeline')
        if st.button('Ouvrir l’historique complet', icon=':material/history:', key='overview_history'):
            from ui.history_navigation import open_history
            open_history(reset=True)
        match_history_cards(context, static_data, limit=3)
        highlights = promoted_insights(context, limit=3)
        if highlights:
            section_header('Contextes descriptifs complémentaires', 'Au moins 10 parties dans chaque groupe ; ces groupes ne sont pas des fenêtres chronologiques.')
            for observation in highlights:
                discovery_card(observation)


def _profile_action():
    if st.button('Configurer ou importer un profil →', key='overview_start'):
        from pages.profiles import show_profiles
        st.switch_page(st.Page(show_profiles, title='Profils', url_path='profiles'))
    st.caption('Pour essayer sans clé Riot ni données personnelles : fermez cette fenêtre et lancez start_demo.bat depuis le dossier du projet.')


if __name__ == '__main__':
    # A cold deep link can discover pages/ before the native router first runs.
    # Register the shared shell; regular imports only expose the page callable.
    from app import main
    main()
