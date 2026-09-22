"""Lightweight polling UI; the importing thread never calls Streamlit."""
from __future__ import annotations

import math
import time
from pathlib import Path

import streamlit as st

from core.db import Database
from core.import_jobs import JobStore, TERMINAL, cancel_import_job, resume_import_job
from core.import_lock import import_is_running
from core.diagnostics import record_failure, safe_message, export_diagnostic
from analytics.exports import markdown_text


def watch_job(path, job_id, state):
    """Register immediately, even if the worker finishes before the first poll."""
    _observer_scope(path, state)
    state['job_monitor_pending'] = job_id


def _observer_scope(path, state):
    scope = str(Path(path).resolve())
    if state.get('job_monitor_scope') != scope:
        for key in list(state):
            if str(key).startswith('job_monitor_'):
                del state[key]
        state['job_monitor_scope'] = scope


def observe_job(store, state):
    """Library-scoped observer survives profile switches, never DB switches."""
    _observer_scope(store.database.path, state)
    tracked = store.get(state['job_monitor_pending']) if state.get('job_monitor_pending') else None
    if tracked is None:
        state.pop('job_monitor_pending', None)
    # Deliver this session's completion even if another profile has a paused job.
    if tracked and tracked['status'] in TERMINAL:
        return tracked
    return store.foreground()


@st.fragment(run_every=1)
def render_import_status(settings, puuid):
    database = Database(settings.database_path)
    store = JobStore(database)
    job = observe_job(store, st.session_state)
    running = import_is_running(database.path)
    previous_busy = st.session_state.get('job_monitor_busy', running)
    st.session_state['job_monitor_busy'] = running
    if previous_busy != running:
        # Buttons outside this fragment must also become enabled/disabled again.
        st.rerun()
    if not job:
        return
    pending = store.pending()
    if not running and job['status'] not in TERMINAL and len(pending) > 1:
        labels = {row['job_id']: f'{row["game_name"]}#{row["tag_line"]} · {row["kind"]}' for row in pending}
        if st.session_state.get('job_monitor_selected') not in labels:
            st.session_state['job_monitor_selected'] = job['job_id'] if job['job_id'] in labels else next(iter(labels))
        selected = st.selectbox('Import à reprendre', list(labels), format_func=labels.get, key='job_monitor_selected')
        job = next(row for row in pending if row['job_id'] == selected)
    job_id, status = job['job_id'], job['status']
    with st.container(border=True):
        st.caption('RANG PERSONNEL · observation datée' if job['kind'] == 'profile_rank' else 'INSTANTANÉ DES RANGS · progression sauvegardée' if job['kind'] == 'ranks' else 'IMPORT LOCAL · progression sauvegardée')
        st.markdown(f'Profil du job : **{markdown_text(job["game_name"])}#{markdown_text(job["tag_line"])}**')
        if job['puuid'] and job['puuid'] != puuid and database.get_player(job['puuid']):
            if st.button('Voir le profil de cet import', key='job_monitor_profile_' + job_id, width='stretch'):
                from ui.profiles import activate_profile
                from config.settings import get_settings
                base = get_settings()
                primary = database.find_player(base.riot_game_name, base.riot_tag_line)
                activate_profile(None if primary and primary['puuid'] == job['puuid'] else job['puuid'])
                st.rerun()
        if job['kind'] == 'profile_rank':
            st.progress(min(1.0, job['completed'] / job['total']) if job['total'] else 0,
                        text='Observation personnelle enregistrée' if job['completed'] else 'Récupération du rang personnel')
        elif job['kind'] == 'ranks':
            st.progress(job['completed'] / job['total'] if job['total'] else 0,
                        text=f'{job["completed"]} / {job["total"] or "…"} joueurs vérifiés')
        elif job['kind'] == 'backfill' and job['target']:
            current, total = job['local_count'], job['target']
            st.progress(min(1.0, current / total), text=f'{current} / {total} parties locales')
        elif job['phase'] == 'importing' and job['total']:
            st.progress(min(1.0, job['completed'] / job['total']),
                        text=f'{job["completed"]} / {job["total"]} nouvelles parties')
        else:
            st.caption(f'{job["local_count"]} parties locales · recherche de l’historique' if status not in TERMINAL else f'{job["local_count"]} parties locales')
        if status == 'rate_limited' and running:
            try:
                remaining = max(0, math.ceil(store.cooldown() - time.time()))
            except (ValueError, OverflowError):
                st.error('[PAYLOAD_INVALID] Délai Riot local invalide. Annulez le job et faites vérifier la base ; aucun délai ne sera ignoré.')
            else:
                st.info(f'Limite Riot atteinte — reprise dans {remaining} s. Vous n’avez rien à recliquer.')
        elif status == 'cancel_requested':
            st.info('Annulation demandée. Arrêt à la prochaine limite sûre, après l’appel réseau en cours.')
        elif status in TERMINAL:
            st.success(job['message'] or 'Import terminé.') if status == 'completed' else st.info(job['message'])
        elif status == 'paused' or not running:
            st.warning(job['message'] or 'Import interrompu ou application redémarrée. La progression est conservée.')
        else:
            st.caption('Import en arrière-plan. Vous pouvez naviguer dans l’application.')
        if job.get('diagnostic_json'):
            with st.expander('Diagnostic expurgé'):
                payload = export_diagnostic(job['diagnostic_json'])
                st.code(payload, language='json')
                st.download_button('Télécharger le diagnostic', payload, file_name='lol-analytics-diagnostic.json',
                                   mime='application/json', key='job_monitor_diagnostic_' + job_id)
                st.caption('Sans clé, Riot ID, PUUID, réponse réseau ni chemin local. L’intégrité SQLite n’est pas testée par ce diagnostic.')
        if status not in TERMINAL:
            if not running and status != 'cancel_requested':
                if st.button('Reprendre l’import', key='library_job_resume_' + job_id, width='stretch'):
                    try:
                        st.session_state['job_monitor_pending'] = resume_import_job(settings, job_id)
                    except Exception as error:
                        record_failure(error, phase='resume')
                        st.error(safe_message(error))
                    else:
                        st.rerun()
            if st.button('Annuler l’import', key='library_job_cancel_' + job_id, width='stretch',
                         disabled=status == 'cancel_requested' and running):
                try:
                    cancel_import_job(settings, job_id)
                except Exception as error:
                    record_failure(error, phase='cancel')
                    st.error(safe_message(error))
                else:
                    st.rerun()
        other_pending = sum(row['job_id'] != job_id for row in pending)
        if other_pending:
            st.caption(f'{other_pending} autre(s) import(s) en attente. Un seul worker utilise Riot à la fois.')
        if status not in TERMINAL:
            st.session_state['job_monitor_pending'] = job_id
        if st.session_state.get('job_monitor_pending') == job_id and status in TERMINAL:
            st.session_state.pop('job_monitor_pending', None)
            st.cache_data.clear()
            st.session_state['library_flash'] = f'{markdown_text(job["game_name"])}#{markdown_text(job["tag_line"])} · {job["message"] or "Import terminé."}'
            if status == 'completed' and job['puuid'] and job['kind'] not in {'ranks', 'profile_rank'} and job['local_count'] > job['local_before']:
                recent = database.player_matches(job['puuid'], 1)
                st.session_state['review_post_sync'] = {'owner': job['puuid'], 'anchor': recent[0]['match_id'] if recent else None}
            st.rerun()
