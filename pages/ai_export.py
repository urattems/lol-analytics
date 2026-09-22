"""Clear full-library and custom-selection downloads for the active profile."""
from __future__ import annotations

from datetime import datetime
from html import escape

import streamlit as st
from pydantic import ValidationError

from analytics.ai_bundle import BUNDLE_FILES, BundleDatasetMismatch, ai_bundle_filename, build_ai_bundle
from analytics.explorer import AnalysisFilters
from analytics.exports import build_export_payload, build_full_export_payload, export_filename, export_json, export_markdown
from core.db import Database
from core.static_data import get_static_data_service
from ui.analysis_filters import render_analysis_filter_controls
from ui.components import empty_state, page_header, stat_strip
from ui.formatting import decimal_label
from ui.page_helpers import cached_context_dataset, database_revision
from ui.profiles import get_active_settings, get_active_player


@st.cache_data(show_spinner=False, max_entries=3)
def _cached_bundle(path: str, puuid: str, riot_id: str, platform: str, routing: str, revision: int) -> bytes:
    games = cached_context_dataset(path, puuid, revision)
    return build_ai_bundle(games, Database(path), puuid, riot_id, platform, routing, item_resolver=get_static_data_service().item)


def _choose_filters(all_games) -> AnalysisFilters:
    explorer_state = st.session_state.get("explorer_filters")
    modes = ["Nouvelle sélection"]
    if explorer_state:
        modes.insert(0, "Sélection actuelle de l’Explorer")
    mode = st.radio("Sélection à exporter", modes, horizontal=True, key="export_selection_source")
    if mode == "Sélection actuelle de l’Explorer":
        try:
            filters = AnalysisFilters.model_validate(explorer_state)
        except ValidationError:
            st.warning("Cette sélection n’est plus valide. Recréez vos filtres ci-dessous.")
        else:
            st.caption("Tous les filtres de l’Explorer sont repris à l’identique pour ce profil.")
            return filters
    filters, inverted = render_analysis_filter_controls(all_games, "export", "Construire la sélection")
    if inverted:
        st.caption("Bornes de durée inversées pour conserver une plage valide.")
    return filters


def show_ai_export() -> None:
    settings = get_active_settings()
    database = Database(settings.database_path)
    player = get_active_player(database, settings)
    page_header("Vos données, prêtes à partager.", "Un dossier complet pour votre outil d’analyse, ou une sélection précise de parties.", "AI EXPORT")
    if not player:
        empty_state("Un historique à constituer", "Synchronisez ce profil pour préparer un export. Les exports portent toujours sur le joueur affiché en haut de page.")
        return
    puuid = str(player["puuid"])
    revision = database_revision(settings.database_path)
    all_games = cached_context_dataset(str(settings.database_path), puuid, revision)
    timeline_ids = database.timeline_available_match_ids(puuid)
    mode = st.segmented_control("Portée de l’export", ["Tout l’historique", "Sélection personnalisée"], default="Tout l’historique", key="ai_export_mode") or "Tout l’historique"
    generated = datetime.now().astimezone()
    static = get_static_data_service()
    if mode == "Tout l’historique":
        st.markdown(
            '<div class="la-export-card"><div class="la-eyebrow">DOSSIER COMPLET · RECOMMANDÉ</div>'
            f'<div class="la-display-title">{all_games.height} parties. Tout le contexte.</div>'
            f'<div class="la-meta">Tout l’historique local de <b>{escape(settings.riot_id)}</b>, toutes les durées et tous les champions.</div>'
            f'<div class="la-export-checks"><span>✓ {len(timeline_ids)} timelines</span><span>✓ {len(BUNDLE_FILES)} fichiers d’analyse</span>'
            '<span>✓ Identités externes remplacées par des alias</span></div></div>', unsafe_allow_html=True,
        )
        with st.spinner("Préparation du dossier complet…"):
            try:
                bundle = _cached_bundle(str(settings.database_path), puuid, settings.riot_id, settings.riot_platform_region, settings.riot_routing_region, revision)
            except BundleDatasetMismatch:
                st.warning("L’historique a changé pendant la préparation. Rechargez la page pour exporter la bibliothèque à jour.")
                return
        st.download_button("Télécharger le dossier IA complet (.zip)", bundle, file_name=ai_bundle_filename(settings.riot_id, generated), mime="application/zip", type="primary", width="stretch")
        st.caption("Matchs, champions, builds, matchups, coéquipiers, sessions, trajectoires et événements. Le fichier reste local jusqu’à ce que vous le partagiez.")
        payload = build_full_export_payload(all_games, settings.riot_id, settings.riot_platform_region, settings.riot_routing_region, generated, static.item, timeline_ids)
    else:
        st.markdown('<div class="la-hero"><div class="la-eyebrow">SÉLECTION PERSONNALISÉE</div><div class="la-title">Une question précise, un export ciblé.</div><div class="la-meta">Seules les parties correspondant aux filtres ci-dessous seront exportées.</div></div>', unsafe_allow_html=True)
        filters = _choose_filters(all_games)
        payload = build_export_payload(all_games, filters, settings.riot_id, settings.riot_platform_region, settings.riot_routing_region, generated, static.item, timeline_ids)
    summary, selection = payload["summary"], payload["selection"]
    st.subheader("Aperçu du contenu")
    stat_strip([
        ("Parties exportées / locales", f'{summary["games"]} / {all_games.height}'),
        ("Winrate", decimal_label(summary["winrate"], 1, " %")),
        ("KDA", decimal_label(summary["kda"], 2)),
        ("Champions", str(len(payload["champions"]))),
    ])
    if mode != "Tout l’historique":
        st.caption(f'Sélection personnalisée : {summary["games"]} parties exportées. L’historique local complet en contient {all_games.height}.')
    left, right = st.columns(2)
    left.download_button("Exporter les matchs en JSON", export_json(payload), file_name=export_filename(settings.riot_id, int(summary["games"]), "json", generated), mime="application/json", width="stretch")
    right.download_button("Exporter le rapport Markdown", export_markdown(payload), file_name=export_filename(settings.riot_id, int(summary["games"]), "md", generated), mime="text/markdown", width="stretch")
    with st.expander("Vérifier la sélection et le contenu"):
        st.json(selection, expanded=False)
        st.caption("Le dossier complet contient : " + ", ".join(sorted(BUNDLE_FILES)))
    st.caption("Observations descriptives uniquement. Le Riot ID du profil analysé reste dans l’export ; les identités des autres joueurs sont remplacées par des alias.")


if __name__ == '__main__':
    # A cold deep link can discover pages/ before the native router first runs.
    # Register the shared shell; regular imports only expose the page callable.
    from app import main
    main()
