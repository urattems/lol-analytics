"""Same-scope matchup details with exact checkpoint and acquisition evidence."""
import polars as pl
import streamlit as st

from analytics.exports import markdown_text
from analytics.matchup_experience import matchup_observations
from analytics.progression import METRICS, compare_timings
from analytics.stuff import DEFINITION_VERSION, STATE_LABELS
from ui.formatting import decimal_label, duration_label
from ui.page_helpers import database_revision
from ui.progression import source_button
from ui.stuff import cached_stuff


def render_champion_matchups(games, database, owner):
    st.caption('Adversaire unique du même rôle déclaré ; indisponible si ambigu. Ce n’est pas une détection certaine du duel de lane, ni une tier list de counters.')
    matrix = matchup_observations(games)
    if not matrix:
        st.info('Aucun matchup identifiable dans cette sélection.')
        return
    st.dataframe([{'Adversaire': row['opponent'], 'N': row['n'], 'V / D': f'{row["wins"]} / {row["losses"]}',
                   'GD @15 médian': decimal_label(row['metrics']['gold_diff_15']['value'], 0),
                   'N GD @15': row['metrics']['gold_diff_15']['n'],
                   'CS @15 médian': decimal_label(row['metrics']['cs_diff_15']['value'], 1),
                   'N CS @15': row['metrics']['cs_diff_15']['n']} for row in matrix], width='stretch', hide_index=True)
    opponents = [row['opponent'] for row in matrix]
    if st.session_state.get('champion_matchup_source') not in opponents:
        st.session_state['champion_matchup_source'] = opponents[0]
    opponent = st.selectbox('Adversaire à examiner', opponents, key='champion_matchup_source')
    row = next(row for row in matrix if row['opponent'] == opponent)
    if row['n'] < 10:
        st.info(f'Historique limité · N={row["n"]}. Pas de conclusion « favorable » ou « counter » sur ce groupe.')
    else:
        st.caption(f'Souvent rencontré dans cette sélection · N={row["n"]}, comparaison descriptive.')
    st.caption('État personnel @10 · ' + ' · '.join(f'{label} : {row["states"][state]}' for state, label in STATE_LABELS.items()))
    st.caption('Avance ≥ +500 or, retard ≤ −500, face au même rôle. Les données manquantes ne deviennent pas « équilibré ».')
    source_button(owner, row['source_ids'], f'Matchup contre {opponent}', 'champion_matchup_open', origin='champions')
    metric = st.selectbox('Checkpoint / métrique', list(row['metrics']), format_func=lambda k: METRICS[k].label, key='champion_matchup_metric')
    sample = row['metrics'][metric]
    st.caption(f'{METRICS[metric].label} · {"Moyenne" if metric == "kda" else "Médiane"} {decimal_label(sample["value"], 2)} '
               f'· P25 {decimal_label(sample["p25"], 2)} · P75 {decimal_label(sample["p75"], 2)} · N={sample["n"]}')
    source_button(owner, sample['source_ids'], f'{METRICS[metric].label} contre {opponent} · valeurs exploitables',
                  'champion_matchup_metric_sources', origin='champions')
    with st.expander('Premier item · cet adversaire / autres adversaires identifiés'):
        patches = tuple(sorted(games['patch'].drop_nulls().unique().to_list()))
        catalogs, facts = cached_stuff(str(database.path), owner, patches, database_revision(database.path),
                                     DEFINITION_VERSION, bool(st.session_state.get('champions_include_short_games')))
        matched = games.filter(pl.col('opponent_champion') == opponent)
        others = games.filter(pl.col('opponent_champion').is_not_null() & (pl.col('opponent_champion') != opponent))
        comparisons = compare_timings(matched, others, facts)
        if not comparisons:
            st.info('Pas d’achats qualifiés dans ces groupes. Les catalogues du patch se chargent dans Stuff & timings.')
            return
        def label(i):
            group = comparisons[i]
            return f'{catalogs[group["patch"]].items[group["item_id"]].name} · {group["role"]} · {group["patch"]}'
        if st.session_state.get('champion_matchup_timing_group') not in range(len(comparisons)):
            st.session_state['champion_matchup_timing_group'] = 0
        index = st.selectbox('Même objet, rôle et patch', range(len(comparisons)), format_func=label, key='champion_matchup_timing_group')
        comparison = comparisons[index]
        for column, key, title in zip(st.columns(2), ('recent', 'previous'), (f'Contre {opponent}', 'Autres adversaires')):
            with column:
                sample = comparison[key]
                st.markdown('**' + markdown_text(title) + '**')
                if not sample:
                    st.caption('Aucune acquisition comparable · N=0')
                    continue
                st.metric('Timing médian', duration_label(sample['median']))
                st.caption(f'N={sample["n"]} · P25 {duration_label(sample["p25"])} · P75 {duration_label(sample["p75"])}')
                if sample['small_sample']:
                    st.caption('Petit échantillon : moins de 5 acquisitions.')
                source_button(owner, sample['source_ids'], title + ' · premier item · ' + label(index),
                              'champion_matchup_timing_' + key, origin='champions')
        st.caption(f'Écart contre cet adversaire − autres : {decimal_label(comparison["delta"], 1, " s")}. '
                   'Même champion/rôle/patch/objet, files selon le filtre de la fiche. Association descriptive, pas une cause.')
