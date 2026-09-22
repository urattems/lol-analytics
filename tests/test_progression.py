"""Disjoint windows, denominators, missing data, patch boundaries and proofs."""
import polars as pl
import pytest

from analytics.progression import ProgressionScope, compare_windows, compare_metrics, summarize_metric, window_context, home_observation


def games(count, **overrides):
    return pl.from_dicts([dict(match_id=f'M{i:03}', game_creation=1_780_000_000_000 + i * 2_400_000,
        champion='Shyvana', role='JUNGLE', queue_id=420, patch='16.17', duration=1800,
        kills=6, deaths=3, assists=9, win=i % 2, cs_total=180, vision_score=30, damage_dealt=15000,
        gold_diff_15=i*50, gold_diff_10=i*25, cs_diff_15=5, cs_diff_10=3, timeline_available=True) | overrides
        for i in range(count)], infer_schema_length=None) if count else games(1).head(0)


@pytest.mark.parametrize('count', [0, 1, 7, 20, 27, 40])
def test_real_counts_disjoint_windows(count):
    result = compare_windows(games(count), ProgressionScope(recent_count=20, previous_count=20))
    assert result.recent.height == min(20, count)
    assert result.previous.height == max(0, min(20, count-20))
    assert not set(result.recent['match_id']) & set(result.previous['match_id'])
    assert result.recent['match_id'].to_list() == [f'M{i:03}' for i in reversed(range(max(0, count-20), count))]


def test_filter_before_window_and_stable_ties():
    frame = games(40).with_columns(pl.lit(1000).alias('game_creation'),
        pl.when(pl.int_range(pl.len()) % 2 == 0).then(pl.lit('Other')).otherwise(pl.col('champion')).alias('champion'))
    scope = ProgressionScope(champion='Shyvana', recent_count=7, previous_count=20)
    result = compare_windows(frame, scope)
    assert result.recent.height == 7 and result.previous.height == 13
    assert result.recent['match_id'][0] == 'M039'
    assert compare_windows(frame.reverse(), scope).recent.equals(result.recent)


def test_invalid_dates_are_not_assigned_to_windows():
    frame = games(7).with_columns(pl.Series('game_creation', [None, -1, 10**18, 1000, 2000, 3000, 4000]))
    result = compare_windows(frame, ProgressionScope())
    assert result.undated_count == 3 and result.recent.height == 4
    assert window_context(result.recent)['first'] == 1000


@pytest.mark.parametrize('values,expected', [([1., 1.5, float('nan'), float('inf')], 1),
                                           (['1000', 'tomorrow', None, ''], 0)])
def test_no_silent_coercion_of_malformed_dates(values, expected):
    result = compare_windows(games(4).with_columns(pl.Series('game_creation', values)), ProgressionScope())
    assert result.recent.height == expected and result.undated_count == 4 - expected


def test_patch_default_specific_mixed_and_unknown():
    frame = games(30).with_columns(pl.Series('patch', ['16.16']*18 + ['16.17']*10 + [None]*2))
    default = compare_windows(frame, ProgressionScope())
    assert default.effective_patch == '16.17' and default.recent.height == 10 and default.previous.height == 0
    assert default.excluded_patch_count == 20
    mixed = compare_windows(frame, ProgressionScope(patch_policy='mixed'))
    assert mixed.recent.height == 10 and mixed.previous.height == 20
    assert window_context(mixed.recent)['unknown_patch'] == 2
    specific = compare_windows(frame, ProgressionScope(patch_policy='specific', patch='16.16'))
    assert specific.recent.height == 10 and specific.previous.height == 8
    unknown = compare_windows(games(7).with_columns(pl.lit(None).cast(pl.String).alias('patch')), ProgressionScope())
    assert unknown.recent.is_empty() and unknown.effective_patch is None


def test_metric_denominators_and_quartiles_have_exact_sources():
    frame = games(4).with_columns(pl.Series('duration', [1800, 0, None, 1800]),
        pl.Series('deaths', [0, 3, None, 3]), pl.Series('win', [1, None, 0, None]),
        pl.Series('gold_diff_15', [None, 100, 300, 500]))
    assert summarize_metric(frame, 'cs_per_minute')['source_ids'] == ('M000', 'M003')
    assert summarize_metric(frame, 'kda')['n'] == 3
    assert summarize_metric(frame, 'winrate')['value'] == 50
    gold = summarize_metric(frame, 'gold_diff_15')
    assert (gold['value'], gold['p25'], gold['p75'], gold['n']) == (300, 200, 400, 3)
    empty = summarize_metric(frame.head(0), 'winrate')
    assert empty['value'] is None and empty['n'] == 0


def test_nonfinite_metric_values_not_zero_and_zero_baseline_has_absolute_delta():
    frame = games(4).with_columns(pl.Series('gold_diff_15', [float('nan'), float('inf'), -float('inf'), 0.]))
    assert summarize_metric(frame, 'gold_diff_15')['source_ids'] == ('M003',)
    pair = compare_metrics(compare_windows(games(2), ProgressionScope(recent_count=1, previous_count=1)))['gold_diff_15']
    assert pair['previous']['value'] == 0 and pair['delta'] == 50
    assert 'percent_change' not in pair


def test_short_policy_scope_and_duplicates():
    frame = games(7).with_columns(pl.lit(100).alias('duration'))
    assert compare_windows(frame, ProgressionScope()).recent.is_empty()
    assert compare_windows(frame, ProgressionScope(include_short=True)).recent.height == 7
    with pytest.raises(ValueError):
        compare_windows(pl.concat([games(1), games(1)]), ProgressionScope())
    assert compare_windows(games(10), ProgressionScope(role='TOP')).recent.is_empty()


def test_home_never_promotes_tiny_or_cross_patch_samples():
    assert home_observation(games(7)) is None
    result = home_observation(games(30))
    assert result['recent']['n'] == 10 and result['previous']['n'] == 20
    assert result['key'] == 'gold_diff_15'
    repeated = home_observation(games(30))
    assert result['recent'] == repeated['recent'] and result['previous'] == repeated['previous']
    assert result['comparison'].recent.equals(repeated['comparison'].recent)
    frame = games(30).with_columns(pl.Series('patch', ['16.16']*21 + ['16.17']*9))
    assert home_observation(frame) is None


@pytest.mark.parametrize('kwargs', [{'recent_count': 0}, {'previous_count': True}, {'recent_count': 1001},
                                   {'patch_policy': 'guess'}, {'patch_policy': 'specific'}])
def test_invalid_scope_rejected(kwargs):
    with pytest.raises(ValueError):
        ProgressionScope(**kwargs)
