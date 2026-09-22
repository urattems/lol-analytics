"""Explicit, dated rank enrichment. Reading a card never requests Riot ranks."""
from datetime import datetime

import streamlit as st

from core.import_jobs import start_import_job
from core.import_lock import import_is_running


def observation_label(context) -> str:
    if context.first_observed is None:
        return 'Rangs non récupérés'
    try:
        first = datetime.fromtimestamp(context.first_observed).astimezone().strftime('%d/%m/%Y à %H:%M')
        last = datetime.fromtimestamp(context.last_observed).astimezone().strftime('%d/%m/%Y à %H:%M')
    except (ValueError, OverflowError, OSError, TypeError):
        return 'Date de récupération indisponible'
    return f'Observés le {first}' if first == last else f'Observés du {first} au {last}'


def render_rank_summary(context):
    if not context or not context.observed:
        return
    st.caption(f'Votre rang : {context.own_label} · Lobby moyen : {context.lobby_label} · {context.ranked}/{context.total} rangs exploitables')
    st.caption(f'{observation_label(context)} · pas le rang historique au moment de la partie · pas une estimation du MMR.')


def render_rank_panel(context, settings, owner, *, key_prefix='history'):
    if context is None:
        return
    st.markdown('**Contexte classé · instantané daté**')
    if context.observed:
        render_rank_summary(context)
        st.caption('Niveau observé du lobby · agrégats descriptifs, pas des badges attribués')
        from ui.components import stat_strip
        stat_strip([('Médiane ≈', context.median_label), ('Moyenne ≈', context.lobby_label),
                    ('Plage ≈', context.minimum_label + ' → ' + context.maximum_label)])
        st.caption(f'{context.observed} / {context.total} joueurs observés · {context.ranked} classés exploitables · '
                   f'{context.unranked} non classés · {context.unavailable} indisponibles · {context.missing} non récupérés')
        if context.distribution:
            st.caption('Rangs individuels observés : ' + ' · '.join(f'{label} ×{count}' for label, count in context.distribution))
        st.caption('Les agrégats apex restent Master+ ; aucun seuil Grandmaster ou Challenger n’est déduit. '
                   'Instantané actuel daté, ni rang historique de la partie ni MMR.')
    else:
        st.caption('Votre rang et celui du lobby ne sont pas encore récupérés pour cette partie.')
    remaining = context.total - context.observed
    if context.total != 10:
        st.caption(f'Composition locale partielle : {context.total} participants connus, 10 attendus.')
    if remaining:
        st.caption(f'{remaining} joueur(s) non vérifié(s). Enrichir consulte les rangs actuels sur le serveur de cette partie et conserve leur date de récupération. Cela ne reconstitue pas les rangs anciens.')
        busy = import_is_running(settings.database_path)
        if st.button('Compléter l’instantané des rangs' if context.observed else 'Récupérer les rangs actuels',
                     key=f'{key_prefix}_rank_enrich_{context.match_id}',
                     disabled=settings.demo_mode or not settings.riot_api_key or busy):
            try:
                from ui.import_jobs import watch_job
                job_id = start_import_job(settings, 'ranks', None, match_id=context.match_id, owner=owner)
                watch_job(settings.database_path, job_id, st.session_state)
            except ValueError as error:
                st.error(str(error))
            except Exception:
                st.error('L’enrichissement ne peut pas démarrer. Vérifiez la clé et attendez la fin de l’autre import.')
            else:
                st.rerun()
    else:
        st.caption('Instantané conservé : les futures consultations ne le remplacent pas et ne font aucun appel Riot.')
