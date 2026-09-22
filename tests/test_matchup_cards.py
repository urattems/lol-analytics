import polars as pl
import pytest

from analytics.matchup_cards import matchup_cards, sample_label
from tests.test_explorer import _games


@pytest.mark.parametrize('n,label', [(0, 'Faible'), (4, 'Faible'), (5, 'Limité'), (9, 'Limité'), (10, 'Modéré'), (24, 'Modéré'), (25, 'Solide descriptivement')])
def test_sample_thresholds(n, label):
    assert sample_label(n) == label


def test_matchup_view_model_sort_missing_values_and_actual_n():
    games = _games().with_columns(pl.Series('opponent_champion', ['Vi', 'Vi', 'LeeSin', 'LeeSin', 'Vi', 'Warwick']),
                                 pl.Series('gold_diff_15', [None, 100, None, None, 200, -400]),
                                 pl.lit(True).alias('timeline_available'))
    cards = matchup_cards(games, 'gold_diff_15', 'gold_diff_15')
    assert cards[-1].value is None
    assert cards[0].value >= cards[1].value
    assert any(c.observed < c.games for c in cards)
    assert all(c.champion and c.opponent and c.sample == 'Faible' for c in cards)
    with pytest.raises(ValueError):
        matchup_cards(games, 'not-a-metric')
    assert matchup_cards(games.head(0)) == []
