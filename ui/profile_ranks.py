"""Intentional rank capture and point-only, queue-separated chronology."""
from datetime import datetime, timezone
from html import escape

import plotly.graph_objects as go
import streamlit as st

from core.profile_ranks import load_profile_ranks
from core.ranks import rank_label, rank_score
from core.import_lock import import_is_running
from ui.charts import chart_style


def rank_date(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).strftime('%d/%m/%Y %H:%M UTC')


def rank_figure(rows):
    ranked = [row for row in rows if row['status'] == 'ranked' and rank_score(row) is not None]
    figure = go.Figure(go.Scatter(x=[datetime.fromtimestamp(row['observed_at'], timezone.utc).isoformat() for row in ranked],
        y=[rank_score(row) for row in ranked], mode='markers', marker={'size': 10, 'color': '#58d9c2'},
        customdata=[[escape(rank_label(row)), rank_date(row['observed_at'])] for row in ranked],
        hovertemplate='%{customdata[0]}<br>%{customdata[1]}<extra></extra>'))
    figure.update_xaxes(title='Date de récupération réelle', type='date')
    figure.update_yaxes(title='Échelle de rang affiché · pas du MMR', tickmode='array',
        tickvals=[0, 400, 800, 1200, 1600, 2000, 2400, 2800],
        ticktext=['Iron IV', 'Bronze IV', 'Silver IV', 'Gold IV', 'Platinum IV', 'Emerald IV', 'Diamond IV', 'Master+'])
    return chart_style(figure, 290)


def render_profile_ranks(database, settings, owner):
    queue = st.selectbox('File classée', [420, 440], format_func=lambda v: 'Solo / Duo' if v == 420 else 'Flex', key='progression_rank_queue')
    rows = load_profile_ranks(database, owner, settings.riot_platform_region, queue)
    st.caption(f'Serveur {settings.riot_platform_region} · Observations du rang personnel, distinctes des instantanés de lobby. '
               'Aucune reconstruction du rang à la date d’une ancienne partie.')
    st.caption('Tous les points collectés pour ce profil, cette file et ce serveur ; les filtres Champion et fenêtres de parties ne filtrent pas cette chronologie.')
    if st.button('Capturer mon rang actuel', key='progression_rank_capture',
                 disabled=settings.demo_mode or not settings.riot_api_key or import_is_running(database.path)):
        from core.import_jobs import start_import_job
        from core.diagnostics import record_failure, safe_message
        from ui.import_jobs import watch_job
        try:
            job_id = start_import_job(settings, 'profile_rank', queue, owner=owner)
            watch_job(database.path, job_id, st.session_state)
        except Exception as error:
            record_failure(error, phase='profile_rank', kind='profile_rank')
            st.error(safe_message(error))
        else:
            st.rerun()
    if settings.demo_mode:
        st.caption('Démo : capture Riot désactivée. Les points de démonstration, lorsqu’ils sont présents, sont inventés et étiquetés.')
    elif not settings.riot_api_key:
        st.caption('Une clé Riot est nécessaire pour ajouter une observation ; les points déjà enregistrés restent consultables.')
    if not rows:
        st.info('Aucun rang personnel collecté pour cette file et ce serveur. Les snapshots de matchs ne sont pas recopiés rétroactivement ici.')
        return
    latest = rows[-1]
    st.metric('Dernière observation', rank_label(latest))
    st.caption(rank_date(latest['observed_at']) + (' · donnée synthétique' if latest['source'] == 'synthetic-demo' else ' · League-V4'))
    st.plotly_chart(rank_figure(rows), width='stretch', config={'displayModeBar': False})
    st.caption('Points observés uniquement, aucune ligne interpolée entre deux dates. 100 unités par division puis les LP pour situer les points ; '
               'les badges Master/Grandmaster/Challenger sont ceux reçus, jamais déduits d’un seuil de LP.')
    st.dataframe([{'Date de récupération': rank_date(row['observed_at']), 'Rang': rank_label(row),
                   'Statut': {'ranked': 'Classé', 'unranked': 'Non classé', 'unavailable': 'Indisponible'}[row['status']],
                   'Source': 'Démo synthétique' if row['source'] == 'synthetic-demo' else 'League-V4'} for row in reversed(rows)],
                  width='stretch', hide_index=True)
