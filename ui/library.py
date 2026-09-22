"""Sidebar controls for new-match sync and voluntary historical backfill."""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from config.settings import Settings
from ui.profiles import get_active_player
from core.db import Database
from core.diagnostics import record_failure, safe_message
from core.sync import ImportProgress, backfill_from_settings, sync_from_settings
from core.import_jobs import start_import_job
from core.import_lock import ImportBusyError, import_is_running
from ui.import_jobs import render_import_status, watch_job
from core.timeline import TimelineProgress, enrich_timelines_from_settings


BACKFILL_TARGETS: dict[str, int | str | None] = {
    "50 matchs": 50,
    "100 matchs": 100,
    "200 matchs": 200,
    "500 matchs": 500,
    "Personnalisé": "custom",
    "Tout l'historique disponible": None,
}
TIMELINE_TARGETS: dict[str, int | str | None] = {
    "20 dernières": 20,
    "50 dernières": 50,
    "100 dernières": 100,
    "Tout l’historique local": None,
    "Personnalisé": "custom",
}


def _riot_error_message(error: Exception) -> str:
    if isinstance(error, ImportBusyError):
        return str(error)
    return safe_message(error)


def _progress_callback() -> Callable[[ImportProgress], None]:
    progress_bar = st.sidebar.progress(0, text="Préparation…")

    def update(event: ImportProgress) -> None:
        if event.phase == "discovering":
            progress_bar.progress(
                0,
                text=f"Recherche des Match IDs… {event.discovered} manquant(s) trouvé(s)",
            )
            return
        total = event.total or 0
        ratio = event.current / total if total else 1.0
        progress_bar.progress(
            min(1.0, ratio), text=f"Import : {event.current} / {total}"
        )

    return update


def _selected_backfill_target(local_count: int) -> int | None:
    selection = st.selectbox(
        "Objectif local",
        list(BACKFILL_TARGETS),
        key="library_backfill_target",
    )
    value = BACKFILL_TARGETS[selection]
    if value == "custom":
        return int(
            st.number_input(
                "Nombre total de matchs",
                min_value=1,
                value=max(50, local_count),
                step=1,
                key="library_custom_target",
            )
        )
    return int(value) if value is not None else None


def _timeline_progress_callback() -> Callable[[TimelineProgress], None]:
    progress_bar = st.sidebar.progress(0, text="Préparation des timelines…")

    def update(event: TimelineProgress) -> None:
        ratio = event.current / event.total if event.total else 1.0
        progress_bar.progress(
            min(1.0, ratio),
            text=(
                f"Timeline {event.current} / {event.total} · "
                f"disponibles {event.available} · indisponibles {event.unavailable} · erreurs {event.errors}"
            ),
        )

    return update


def _selected_timeline_limit(local_count: int) -> int | None:
    selection = st.selectbox(
        "Portée Timeline", list(TIMELINE_TARGETS), key="library_timeline_target"
    )
    value = TIMELINE_TARGETS[selection]
    if value == "custom":
        return int(
            st.number_input(
                "Nombre de timelines",
                min_value=1,
                max_value=max(1, local_count),
                value=min(20, max(1, local_count)),
                step=1,
                key="library_timeline_custom",
            )
        )
    return int(value) if value is not None else None


def render_library_controls(
    settings: Settings, database: Database, local_count: int
) -> None:
    """Render explicit, separate controls; neither operation runs automatically."""

    player = get_active_player(database, settings)
    player_puuid = str(player["puuid"]) if player else None
    coverage = (
        database.timeline_coverage(player_puuid)
        if player_puuid
        else {"available": 0, "local": local_count, "error": 0}
    )
    st.sidebar.markdown(
        f'<div class="la-library"><b>{local_count}</b> parties locales'
        f'<small>{coverage["available"]} / {coverage["local"]} timelines enrichies</small></div>',
        unsafe_allow_html=True,
    )
    if settings.demo_mode:
        st.sidebar.caption("Données de démonstration · synchronisation désactivée")
        return
    busy = import_is_running(database.path)
    with st.sidebar:
        render_import_status(settings, player_puuid)
        if busy:
            st.caption('Un import est en cours pour cette bibliothèque. Les autres imports attendent sa fin.')
    if st.sidebar.button(
        "Synchroniser",
        icon=":material/sync:",
        width="stretch",
        type="primary",
        key="library_sync",
        disabled=busy,
    ):
        try:
            job_id = start_import_job(settings, 'sync', None, owner=player_puuid)
            watch_job(settings.database_path, job_id, st.session_state)
            st.rerun()
        except Exception as error:
            record_failure(error, phase='sync')
            st.sidebar.error(_riot_error_message(error))

    with st.sidebar.expander("Importer l’historique", expanded=False):
        target = _selected_backfill_target(local_count)
        st.caption(f"Actuellement : {local_count} matchs")
        st.caption(
            "Objectif : tout l'historique disponible via Riot"
            if target is None
            else f"Objectif : {target} matchs locaux au total"
        )
        if st.button("Lancer l'import", width="stretch", key="library_backfill", disabled=busy):
            try:
                job_id = start_import_job(settings, 'backfill', target, owner=player_puuid)
                watch_job(settings.database_path, job_id, st.session_state)
                st.rerun()
            except Exception as error:
                record_failure(error, phase='backfill')
                st.error(_riot_error_message(error))

    with st.sidebar.expander("Enrichir les timelines", expanded=False):
        st.caption(
            f"Disponibles : {coverage['available']} / {coverage['local']}  \n"
            f"Manquantes : {coverage.get('missing', 0)} · "
            f"Indisponibles : {coverage.get('unavailable', 0)} · "
            f"Erreurs : {coverage.get('error', 0)}"
        )
        limit = _selected_timeline_limit(local_count)
        retry_errors = st.checkbox(
            "Réessayer les erreurs",
            value=False,
            key="library_timeline_retry_errors",
        )
        if st.button(
            "Lancer l’enrichissement",
            width="stretch",
            key="library_timeline_enrich",
            disabled=player_puuid is None or busy,
        ):
            try:
                result = enrich_timelines_from_settings(
                    settings,
                    player_puuid or "",
                    limit,
                    retry_errors,
                    _timeline_progress_callback(),
                )
                st.cache_data.clear()
                st.session_state["library_flash"] = (
                    "Enrichissement Timeline terminé : "
                    f"{result.available} disponible(s), "
                    f"{result.unavailable} indisponible(s), {result.errors} erreur(s)."
                )
                if result.available:
                    recent = database.player_matches(player_puuid, 1)
                    st.session_state["review_post_sync"] = {"owner": player_puuid, "anchor": recent[0]["match_id"] if recent else None}
                st.rerun()
            except Exception as error:
                record_failure(error, phase='timeline')
                st.error(_riot_error_message(error))
