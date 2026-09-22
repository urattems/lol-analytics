"""Robust matchup summaries and identical item/patch comparison groups."""
from dataclasses import replace

import polars as pl

from analytics.matchup_experience import matchup_observations
from analytics.progression import compare_timings
from tests.test_progression import games
from tests.test_stuff import analysis, purchase, catalog, catalog_payload


def test_matchup_denominators_and_unknown_states_do_not_become_even():
    frame = games(5).with_columns(pl.Series('opponent_champion', ['Vi']*4 + [None]),
        pl.Series('gold_diff_10', [500, 0, -500, None, 999]),
        pl.Series('gold_diff_15', [100, 100, 10000, None, 999]))
    row, = matchup_observations(frame)
    assert row['n'] == 4 and set(row['source_ids']) == {'M000', 'M001', 'M002', 'M003'}
    assert row['states'] == {'ahead': 1, 'even': 1, 'behind': 1, None: 1}
    metric = row['metrics']['gold_diff_15']
    assert metric['n'] == 3 and metric['value'] == 100  # not mean 3 400
    assert (metric['p25'], metric['p75']) == (100, 5050)
    assert metric['source_ids'] == ('M000', 'M001', 'M002')
    assert matchup_observations(frame.head(0)) == []


def test_timing_comparisons_never_merge_patch_role_champion_or_item(catalog):
    base = analysis(catalog, [purchase(3115, 600, 0), purchase(3006, 700, 1)])
    facts = {f'M{i:03}': replace(base, match_id=f'M{i:03}') for i in range(7)}
    facts['M001'] = replace(facts['M001'], patch='16.16')
    facts['M002'] = replace(facts['M002'], role='TOP')
    facts['M003'] = replace(facts['M003'], champion='Vi')
    facts['M004'] = replace(facts['M004'], major_items=(replace(base.major_items[0], item_id=3089),))
    facts['M005'] = replace(facts['M005'], major_items=(replace(base.major_items[0], timestamp_ms=800000),))
    facts['M006'] = replace(facts['M006'], issues=('partial_timeline',))
    groups = compare_timings(games(7), pl.DataFrame({'match_id': ['SYNTHETIC_1']}), {**facts, base.match_id: base})
    assert len(groups) == 5
    comparable, = [row for row in groups if row['previous'] is not None]
    assert comparable['recent']['n'] == 2 and comparable['delta'] == 100
    assert set(comparable['recent']['source_ids']) == {'M000', 'M005'}
    assert comparable['previous']['source_ids'] == ('SYNTHETIC_1',)
    assert all(row['delta'] is None for row in groups if row is not comparable)
    boots = compare_timings(games(1), games(0), facts, slot=0)
    assert boots[0]['recent']['median'] == 700 and boots[0]['previous'] is None
