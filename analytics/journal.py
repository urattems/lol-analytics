"""Descriptive tags, future-game goals and batched recurring-player cohorts."""
import math
from collections import Counter

import polars as pl

from analytics.progression import ProgressionScope, metric_value, summarize_metric, _cohort, MAX_DATE_MS


def tag_observations(games, annotations):
    groups = {}
    fields = ['match_id', 'win', 'kills', 'deaths', 'assists', 'duration']
    for row in games.select(fields).to_dicts():
        for tag in annotations.get(row['match_id'], {}).get('tags', ()):
            groups.setdefault(tag, []).append(row)
    result = []
    for tag, rows in sorted(groups.items()):
        cohort = pl.from_dicts(rows, infer_schema_length=None)
        ids = [row['match_id'] for row in rows]
        result.append({'tag': tag, 'n': len(ids), 'wins': cohort.filter(pl.col('win') == 1).height,
                       'losses': cohort.filter(pl.col('win') == 0).height, 'source_ids': tuple(ids),
                       'kda': summarize_metric(cohort, 'kda'),
                       'deaths': summarize_metric(cohort, 'deaths_per_minute')})
    return result


def goal_progress(games, goal, markers, *, now):
    if goal['metric_key'] is None:
        return {'manual': True, 'observed': [], 'successes': 0, 'failures': 0, 'unknown': 0, 'remaining': None}
    # The creation marker excludes matches that were already known, even if a
    # bad future timestamp exists locally. Historical backfill is not new play.
    if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
        raise ValueError('Horloge locale invalide.')
    scope = ProgressionScope(champion=goal['champion'], role=goal['role'],
        queue_id=goal['queue_id'], patch_policy=goal['patch_policy'], patch=goal['patch'],
        include_short=bool(goal['include_short']))
    # Scope uses the same validity/patch/short rules, but must not truncate the
    # future chronology at the comparison UI's 2 000-match cap.
    cohort = _cohort(games, scope)
    cutoff = min(now, goal['closed_at']) if goal['closed_at'] is not None else now
    rows = [r for r in cohort.to_dicts() if type(r.get('game_creation')) in (int, float)
            and math.isfinite(r['game_creation']) and r['game_creation'] == int(r['game_creation'])
            and goal['created_at'] * 1000 < r['game_creation'] <= min(cutoff*1000, MAX_DATE_MS)
            and markers.get(r['match_id'], 0) > goal['baseline_participant_id']
            and (goal['patch_policy'] == 'mixed' or r.get('patch') == goal['patch'])]
    rows.sort(key=lambda r: (r['game_creation'], r['match_id']))
    observed = []
    for row in rows[:goal['horizon']]:
        value = row.get('deaths') if goal['metric_key'] == 'deaths_per_game' else metric_value(row, goal['metric_key'])
        if type(value) not in (int, float) or not math.isfinite(value) or (goal['metric_key'] == 'deaths_per_game' and value < 0):
            value = None
        passed = None if value is None else value >= goal['target_value'] if goal['comparator'] == 'gte' else value <= goal['target_value']
        observed.append({'match_id': row['match_id'], 'value': value, 'passed': passed,
                         'date': row['game_creation'], 'patch': row.get('patch'), 'timeline': row.get('timeline_available') is True})
    return {'manual': False, 'observed': observed, 'successes': sum(r['passed'] is True for r in observed),
            'failures': sum(r['passed'] is False for r in observed), 'unknown': sum(r['passed'] is None for r in observed),
            'remaining': goal['horizon'] - len(observed)}


def recurring_players(library, selected, relationship='ally', minimum=2):
    if relationship not in ('ally', 'enemy') or type(minimum) is not int or minimum < 2:
        raise ValueError('Relation ou seuil invalide.')
    rows = {row['match_id']: row for row in selected.to_dicts()}
    # Single roster pass; no rescan of all games for each identity.
    contexts = {}
    for match_id in rows:
        for participant in library.rosters.get(match_id, ()):
            key = (match_id, participant.get('puuid'))
            contexts[key] = participant
    result = []
    for (camp, identifier), shared in library.shared.items():
        if camp != relationship:
            continue
        ids = sorted(shared & rows.keys(), key=lambda i: (rows[i].get('game_creation') or -1, i), reverse=True)
        if len(ids) < minimum:
            continue
        champions, roles = Counter(), Counter()
        for match_id in ids:
            row = contexts.get((match_id, identifier), {})
            if row.get('champion'): champions[row['champion']] += 1
            if row.get('role'): roles[row['role']] += 1
        result.append({'puuid': identifier, 'n': len(ids), 'wins': sum(rows[i].get('win') == 1 for i in ids),
                       'losses': sum(rows[i].get('win') == 0 for i in ids),
                       'last_seen': max((rows[i]['game_creation'] for i in ids if rows[i].get('game_creation') is not None), default=None),
                       'champions': champions.most_common(3), 'roles': roles.most_common(3), 'source_ids': tuple(ids)})
    return sorted(result, key=lambda r: (-r['n'], r['puuid']))
