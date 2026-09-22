"""Disjoint, patch-explicit personal comparisons with metric-level evidence."""
from __future__ import annotations

from dataclasses import dataclass
import math

import polars as pl

from analytics.stuff import StuffMatch, quantile, timing_groups

MAX_DATE_MS = 253_370_764_800_000


@dataclass(frozen=True)
class ProgressionScope:
    champion: str | None = None
    role: str | None = None
    queue_id: int | None = None
    patch_policy: str = 'latest'
    patch: str | None = None
    include_short: bool = False
    recent_count: int = 10
    previous_count: int = 20

    def __post_init__(self):
        if self.patch_policy not in {'latest', 'specific', 'mixed'}:
            raise ValueError('Politique de patch invalide.')
        if self.patch_policy == 'specific' and not self.patch:
            raise ValueError('Choisissez un patch précis.')
        if any(type(n) is not int or not 1 <= n <= 1000 for n in (self.recent_count, self.previous_count)):
            raise ValueError('Les fenêtres acceptent 1 à 1 000 parties chacune.')


@dataclass(frozen=True)
class Comparison:
    recent: pl.DataFrame
    previous: pl.DataFrame
    scope: ProgressionScope
    effective_patch: str | None
    undated_count: int
    excluded_patch_count: int


@dataclass(frozen=True)
class Metric:
    label: str
    unit: str = ''
    method: str = 'median'


METRICS = {
    'gold_diff_15': Metric('Gold diff @15', 'or'),
    'gold_diff_10': Metric('Gold diff @10', 'or'),
    'cs_per_minute': Metric('CS/min', method='mean'),
    'deaths_per_minute': Metric('Morts/min', method='mean'),
    'kda': Metric('KDA', method='mean'),
    'vision_per_minute': Metric('Vision/min', method='mean'),
    'damage_per_minute': Metric('Dégâts/min', method='mean'),
    'cs_diff_15': Metric('CS diff @15', 'CS'),
    'cs_diff_10': Metric('CS diff @10', 'CS'),
    'duration': Metric('Durée', 's'),
    'winrate': Metric('Winrate', '%', 'mean'),
}
RATES = {'cs_per_minute': 'cs_total', 'deaths_per_minute': 'deaths',
         'vision_per_minute': 'vision_score', 'damage_per_minute': 'damage_dealt'}


def _finite(value, *, nonnegative=False):
    return (type(value) in (int, float) and math.isfinite(value)
            and (not nonnegative or value >= 0))


def _cohort(games: pl.DataFrame, scope: ProgressionScope):
    if games.is_empty():
        return games
    if ('match_id' not in games.columns or games['match_id'].null_count()
            or games['match_id'].n_unique() != games.height):
        raise ValueError('La cohorte doit contenir des matchs identifiés et uniques.')
    selected = games
    for column, value in (('champion', scope.champion), ('role', scope.role), ('queue_id', scope.queue_id)):
        if value is not None:
            selected = selected.filter(pl.col(column) == value)
    if not scope.include_short:
        selected = selected.filter(pl.col('duration').is_null() | (pl.col('duration') >= 300))
    return selected


def compare_windows(games: pl.DataFrame, scope: ProgressionScope) -> Comparison:
    """Filter first, date second, patch third, then slice; never reuse a match."""
    selected = _cohort(games, scope)
    if selected.is_empty():
        return Comparison(selected, selected, scope, scope.patch if scope.patch_policy == 'specific' else None, 0, 0)
    # Canonical dates are integer milliseconds. Do not coerce strings, NaN or
    # fractional hand-edited dates into an apparently precise chronology.
    date = pl.col('game_creation')
    dated = (selected.filter(date.is_not_null() & date.is_between(0, MAX_DATE_MS) & (date == date.floor()))
             if selected.schema['game_creation'].is_numeric() else selected.head(0))
    dated = dated.sort(['game_creation', 'match_id'], descending=[True, True])
    undated = selected.height - dated.height
    patch = scope.patch if scope.patch_policy == 'specific' else None
    if scope.patch_policy == 'latest':
        known = dated['patch'].drop_nulls().filter(dated['patch'].drop_nulls() != '')
        patch = known[0] if len(known) else None
    filtered = dated
    if scope.patch_policy != 'mixed':
        filtered = dated.filter(pl.col('patch') == patch) if patch else dated.head(0)
    return Comparison(filtered.head(scope.recent_count), filtered.slice(scope.recent_count, scope.previous_count),
                      scope, patch, undated, dated.height - filtered.height)


def metric_value(row: dict, key: str) -> float | None:
    if key not in METRICS:
        raise ValueError('Métrique inconnue.')
    if key == 'winrate':
        return float(row['win'] * 100) if type(row.get('win')) in (int, bool) and row['win'] in (0, 1) else None
    if key == 'kda':
        values = [row.get(k) for k in ('kills', 'assists', 'deaths')]
        return (values[0] + values[1]) / max(1, values[2]) if all(_finite(v, nonnegative=True) for v in values) else None
    if key in RATES:
        numerator, duration = row.get(RATES[key]), row.get('duration')
        return numerator * 60 / duration if _finite(numerator, nonnegative=True) and _finite(duration) and duration > 0 else None
    value = row.get(key)
    return float(value) if _finite(value, nonnegative=key == 'duration') else None


def summarize_metric(games: pl.DataFrame, key: str) -> dict:
    metric = METRICS[key]
    samples = [(row['match_id'], value) for row in games.to_dicts()
               if (value := metric_value(row, key)) is not None and math.isfinite(value)]
    values = [value for _, value in samples]
    return {'value': (sum(values) / len(values) if metric.method == 'mean' else quantile(values, .5)) if values else None,
            'p25': quantile(values, .25), 'p75': quantile(values, .75), 'n': len(values),
            'source_ids': tuple(identifier for identifier, _ in samples)}


def compare_metrics(comparison: Comparison) -> dict[str, dict]:
    result = {}
    for key in METRICS:
        recent, previous = summarize_metric(comparison.recent, key), summarize_metric(comparison.previous, key)
        result[key] = {'recent': recent, 'previous': previous,
                      'delta': recent['value'] - previous['value'] if recent['n'] and previous['n'] else None}
    return result


HIGHLIGHT_PRIORITY = ('gold_diff_15', 'cs_per_minute', 'deaths_per_minute',
                      'gold_diff_10', 'vision_per_minute', 'damage_per_minute', 'kda')


def comparison_highlights(pairs: dict) -> list[dict]:
    """Stable product order; require ten usable observations in each window."""
    return [dict(key=key, **pairs[key]) for key in HIGHLIGHT_PRIORITY
            if key in pairs and pairs[key]['delta'] is not None
            and min(pairs[key]['recent']['n'], pairs[key]['previous']['n']) >= 10][:3]


def window_context(games: pl.DataFrame) -> dict:
    rows = games.to_dicts()
    stamps = [row['game_creation'] for row in rows if _finite(row.get('game_creation')) and 0 <= row['game_creation'] <= MAX_DATE_MS]
    patches = sorted({str(row['patch']) for row in rows if row.get('patch')})
    return {'n': len(rows), 'first': min(stamps) if stamps else None, 'last': max(stamps) if stamps else None,
            'patches': patches, 'unknown_patch': sum(not row.get('patch') for row in rows),
            'timeline_n': sum(row.get('timeline_available') is True for row in rows)}


def compare_timings(recent: pl.DataFrame, previous: pl.DataFrame, facts: dict[str, StuffMatch], *, slot=1) -> list[dict]:
    indexed = []
    for games in (recent, previous):
        values = [facts[i] for i in games['match_id'].to_list() if i in facts] if 'match_id' in games.columns else []
        groups = timing_groups(values, slot=slot)
        indexed.append({(g['champion'], g['role'], g['patch'], g['item_id']): g for g in groups})
    result = []
    for key in sorted(indexed[0].keys() | indexed[1].keys(), key=str):
        current, prior = indexed[0].get(key), indexed[1].get(key)
        result.append({'champion': key[0], 'role': key[1], 'patch': key[2], 'item_id': key[3],
                       'recent': current, 'previous': prior,
                       'delta': current['median'] - prior['median'] if current and prior else None})
    return result


def home_observation(games: pl.DataFrame, *, include_short=False) -> dict | None:
    """Deterministic, minimum-N observation; no score or largest-effect hunt."""
    if games.is_empty():
        return None
    counts = games.group_by(['champion', 'role']).len().sort(['len', 'champion', 'role'], descending=[True, False, False])
    top = counts.row(0, named=True)
    scopes = [ProgressionScope(champion=top['champion'], role=top['role'], include_short=include_short),
              ProgressionScope(role=top['role'], include_short=include_short), ProgressionScope(include_short=include_short)]
    for scope in scopes:
        comparison = compare_windows(games, scope)
        # No silent cross-patch fallback when a patch has too little evidence.
        if comparison.recent.height < 10 or comparison.previous.height < 10:
            continue
        metrics = compare_metrics(comparison)
        for key in ('gold_diff_15', 'cs_per_minute', 'deaths_per_minute'):
            pair = metrics[key]
            if pair['recent']['n'] >= 10 and pair['previous']['n'] >= 10 and pair['delta'] not in (None, 0):
                return {'comparison': comparison, 'key': key, **pair}
    return None


def review_candidate(games: pl.DataFrame) -> dict | None:
    """A transparent single-game review reason, not a population-level insight."""
    rows = [row for row in games.to_dicts() if row.get('timeline_available') is True and row.get('win') == 0
            and _finite(row.get('gold_diff_15')) and row['gold_diff_15'] >= 1000
            and _finite(row.get('game_creation')) and 0 <= row['game_creation'] <= MAX_DATE_MS]
    return max(rows, key=lambda r: (r['game_creation'], r['match_id'])) if rows else None
