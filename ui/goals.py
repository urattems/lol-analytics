"""Small, local goals: immutable scope, future games, explicit missing results."""
import time

import streamlit as st

from analytics.exports import markdown_text
from analytics.journal import goal_progress
from core.journal import GOAL_METRICS, GoalSpec, archive_goal, create_personal_goal, load_goals, participant_markers
from ui.formatting import decimal_label, match_date_label
from ui.history_navigation import open_match_sources
from ui.journal import journal_error
from ui.profile_ranks import rank_date


def render_goals(database, owner, games):
    st.caption('Un repère de travail personnel, pas un coach ni une promesse de rang. Les définitions enregistrées ne changent pas rétroactivement ; archivez puis créez un autre objectif pour changer le périmètre.')
    with st.expander('Créer un objectif'):
        # Keep controls live so changing metric updates legal bounds immediately.
        title = st.text_input('Intitulé', max_chars=120, key='journal_goal_title')
        metric = st.selectbox('Mesure', ['manual', *GOAL_METRICS], format_func=lambda k: 'Objectif libre · sans score automatique' if k == 'manual' else GOAL_METRICS[k][0], key='journal_goal_metric')
        comparator = target = horizon = None
        champion = role = queue = patch = None
        policy, include = 'mixed', False
        if metric != 'manual':
            a, b, c = st.columns(3)
            comparator = a.selectbox('Seuil', ['gte', 'lte'], format_func=lambda v: 'Au moins ≥' if v == 'gte' else 'Au plus ≤', key='journal_goal_comparator')
            low, high = GOAL_METRICS[metric][1:]
            target = b.number_input('Valeur cible', min_value=float(low), max_value=float(high), value=0., step=.5, key='journal_goal_value_' + metric)
            horizon = c.selectbox('Prochaines parties', [5, 10], key='journal_goal_horizon')
            def choice(column, label, field):
                values = sorted(games[field].drop_nulls().unique().to_list())
                selected = column.selectbox(label, ['__all__', *values], format_func=lambda v: 'Tous' if v == '__all__' else str(v), key='journal_goal_' + field)
                return None if selected == '__all__' else selected
            a, b, c = st.columns(3)
            champion, role, queue = choice(a, 'Champion', 'champion'), choice(b, 'Rôle', 'role'), choice(c, 'File', 'queue_id')
            policies = ['specific', 'mixed'] if games['patch'].drop_nulls().len() else ['mixed']
            policy = st.selectbox('Périmètre de patch', policies, format_func=lambda v: 'Un patch fixé' if v == 'specific' else 'Plusieurs patchs · mélange explicite', key='journal_goal_patch_policy')
            if policy == 'specific':
                patches = sorted(games['patch'].drop_nulls().unique().to_list())
                patch = st.selectbox('Patch fixé', patches, index=len(patches)-1, key='journal_goal_patch')
            else:
                st.warning('Les futures parties peuvent traverser plusieurs patchs. Le résultat ne mesure pas uniquement votre progression personnelle.')
            include = st.checkbox('Compter aussi les parties de moins de 5 minutes', key='journal_goal_short')
        if st.button('Créer cet objectif', key='journal_goal_create'):
            try:
                spec = GoalSpec(title, None if metric == 'manual' else metric, comparator, target, horizon, champion, role, queue, policy, patch, include)
                create_personal_goal(database, owner, spec)
            except Exception as error:
                journal_error(error)
            else:
                st.session_state['journal_flash'] = 'Objectif créé. Seules les nouvelles parties jouées après cette création pourront compter.'
                st.rerun()
    goals = load_goals(database, owner)
    if not goals:
        st.info('Aucun objectif pour ce profil. Un objectif libre reste possible sans historique local.')
        return
    archived = st.checkbox('Afficher aussi les objectifs archivés', key='journal_goal_archived')
    goals = [g for g in goals if archived or g['closed_at'] is None]
    if not goals:
        st.info('Tous vos objectifs sont archivés.')
        return
    ids = [g['goal_id'] for g in goals]
    if st.session_state.get('journal_goal_selected') not in ids:
        st.session_state['journal_goal_selected'] = ids[0]
    selected = st.selectbox('Objectif à suivre', ids, format_func=lambda i: next(g['title'] + (' · archivé' if g['closed_at'] else '') for g in goals if g['goal_id'] == i), key='journal_goal_selected')
    goal = next(g for g in goals if g['goal_id'] == selected)
    st.markdown('### ' + markdown_text(goal['title']))
    st.caption('Créé le ' + rank_date(goal['created_at']) + (' · démonstration synthétique' if goal['source'] == 'synthetic-demo' else ''))
    progress = goal_progress(games, goal, participant_markers(database, owner), now=time.time())
    if progress['manual']:
        st.info('Objectif libre : aucune réussite ni progression automatique n’est calculée.')
    else:
        st.caption(f'{GOAL_METRICS[goal["metric_key"]][0]} {"≥" if goal["comparator"] == "gte" else "≤"} {goal["target_value"]} · {goal["horizon"]} prochaines parties · '
                   f'{goal["champion"] or "Tous champions"} · {goal["role"] or "Tous rôles"} · file {goal["queue_id"] or "toutes"} · patch {goal["patch"] if goal["patch_policy"] == "specific" else "mélangé"}')
        known = progress['successes'] + progress['failures']
        st.metric('Réussites sur résultats exploitables', f'{progress["successes"]} / {known}')
        st.caption(f'{len(progress["observed"])} parties observées · {progress["unknown"]} sans mesure · {progress["remaining"]} places restantes dans l’horizon')
        st.markdown(' '.join('✅' if row['passed'] is True else '❌' if row['passed'] is False else '❔' for row in progress['observed']) + ' ⚪' * progress['remaining'])
        st.caption('❔ = mesure manquante, pas échec. ⚪ = partie non encore observée. Les imports d’anciennes parties et les dates absentes ne remplissent pas cet objectif.')
        if goal['patch_policy'] == 'mixed':
            st.caption('Patchs observés : ' + ', '.join(sorted({r['patch'] or 'N/A' for r in progress['observed']})))
        if progress['observed']:
            st.caption(f'Timelines {sum(r["timeline"] for r in progress["observed"])}/{len(progress["observed"])} · l’arrivée d’une Timeline peut compléter une mesure auparavant indisponible.')
            st.dataframe([{'Partie': i+1, 'Date': match_date_label(r['date'], True), 'Patch': r['patch'], 'Valeur': decimal_label(r['value'], 2),
                           'Résultat': 'Indisponible' if r['passed'] is None else 'Atteint' if r['passed'] else 'Non atteint'} for i, r in enumerate(progress['observed'])], hide_index=True, width='stretch')
            st.button('Examiner les parties de cet objectif →', key='journal_goal_sources',
                      on_click=open_match_sources, args=(owner, [r['match_id'] for r in progress['observed']], 'Objectif : ' + goal['title']),
                      kwargs={'origin': 'journal'})
    if goal['closed_at']:
        st.caption('Archivé le ' + rank_date(goal['closed_at']) + ' · aucune partie jouée après cette date ne compte.')
    elif st.button('Archiver cet objectif', key='journal_goal_archive'):
        try:
            archive_goal(database, owner, selected, goal['revision'])
        except Exception as error:
            journal_error(error)
        else:
            st.session_state['journal_flash'] = 'Objectif archivé. Aucune partie supprimée.'
            st.rerun()
