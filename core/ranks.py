"""Dated official-rank snapshots: descriptive context, never a hidden MMR model."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
import time
from statistics import median

from core.db import Database
from core.exceptions import RiotAPIError, RiotNotFoundError


RANKED_QUEUES = {420: 'RANKED_SOLO_5x5', 440: 'RANKED_FLEX_SR'}
TIERS = ('IRON', 'BRONZE', 'SILVER', 'GOLD', 'PLATINUM', 'EMERALD', 'DIAMOND')
DIVISIONS = ('IV', 'III', 'II', 'I')
APEX = ('MASTER', 'GRANDMASTER', 'CHALLENGER')
LABELS = {'IRON': 'Iron', 'BRONZE': 'Bronze', 'SILVER': 'Silver', 'GOLD': 'Gold',
          'PLATINUM': 'Platinum', 'EMERALD': 'Emerald', 'DIAMOND': 'Diamond',
          'MASTER': 'Master', 'GRANDMASTER': 'Grandmaster', 'CHALLENGER': 'Challenger'}


def rank_entry(entries: list[dict], queue_id: int) -> dict:
    if queue_id not in RANKED_QUEUES:
        raise ValueError('Seules les files Ranked Solo/Duo et Flex sont prises en charge.')
    chosen = [row for row in entries if row.get('queueType') == RANKED_QUEUES[queue_id]]
    if not chosen:
        return {'status': 'unranked', 'tier': None, 'division': None, 'league_points': None}
    if len(chosen) != 1:
        raise RiotAPIError('Ambiguous ranked queue response')
    row = chosen[0]
    tier, division, points = row.get('tier'), row.get('rank'), row.get('leaguePoints')
    if (tier not in TIERS + APEX or division not in DIVISIONS
            or isinstance(points, bool) or not isinstance(points, int)
            or not 0 <= points <= (1_000_000 if tier in APEX else 100)):
        return {'status': 'unavailable', 'tier': None, 'division': None, 'league_points': None}
    return {'status': 'ranked', 'tier': tier, 'division': division, 'league_points': points}


def rank_label(row: dict | None) -> str:
    if not row:
        return 'Non récupéré'
    if row['status'] != 'ranked':
        return 'Non classé' if row['status'] == 'unranked' else 'Indisponible'
    tier = row['tier']
    division = '' if tier in APEX else ' ' + row['division']
    return f'{LABELS[tier]}{division} · {row["league_points"]} LP'


def rank_score(row: dict) -> int | None:
    if row.get('status') != 'ranked':
        return None
    tier, division, lp = row.get('tier'), row.get('division'), row.get('league_points')
    # Revalidate persisted values; malformed legacy rows must not invent a rank.
    normalized = rank_entry([{'queueType': RANKED_QUEUES[420], 'tier': tier,
                              'rank': division, 'leaguePoints': lp}], 420)
    if normalized['status'] != 'ranked':
        return None
    return (2800 if tier in APEX else TIERS.index(tier) * 400 + DIVISIONS.index(division) * 100) + lp


def average_label(scores: list[int]) -> str:
    if not scores:
        return 'Indisponible'
    mean = math.floor(sum(scores) / len(scores) + 0.5)
    if mean >= 2800:
        # GM/Challenger thresholds vary. Do not infer their badges from LP.
        return f'Master+ · ~{mean - 2800} LP'
    tier = TIERS[mean // 400]
    return f'{LABELS[tier]} {DIVISIONS[(mean % 400) // 100]} · ~{mean % 100} LP'


@dataclass(frozen=True)
class RankContext:
    match_id: str
    queue_id: int
    total: int
    observed: int
    ranked: int
    own_label: str
    lobby_label: str
    first_observed: float | None
    last_observed: float | None
    median_label: str = 'Indisponible'
    minimum_label: str = 'Indisponible'
    maximum_label: str = 'Indisponible'
    unranked: int = 0
    unavailable: int = 0
    missing: int = 0
    distribution: tuple[tuple[str, int], ...] = ()


def load_rank_contexts(database: Database, owner: str, match_ids: list[str]) -> dict[str, RankContext]:
    """Two batched local reads, scoped to the analyzed profile. No Riot calls."""
    ids = list(dict.fromkeys(match_ids))
    if not ids:
        return {}
    if len(ids) > 100:
        raise ValueError('Au plus 100 parties par lecture de contexte de rang.')
    marks = ','.join('?' for _ in ids)
    with database.connection() as connection:
        roster = connection.execute(f'''SELECT m.match_id, m.queue_id, p.puuid
            FROM matches m JOIN participants p ON p.match_id=m.match_id
            WHERE m.match_id IN ({marks}) AND m.queue_id IN (420,440)
            AND EXISTS (SELECT 1 FROM participants own WHERE own.match_id=m.match_id AND own.puuid=?)''', (*ids, owner)).fetchall()
        snapshots = connection.execute(f'''SELECT s.* FROM rank_snapshots s
            JOIN participants own ON own.match_id=s.match_id AND own.puuid=?
            WHERE s.match_id IN ({marks})''', (owner, *ids)).fetchall()
    rosters, rows = defaultdict(list), defaultdict(dict)
    for row in roster:
        rosters[row['match_id']].append(dict(row))
    for row in snapshots:
        rows[row['match_id']][row['puuid']] = dict(row)
    result = {}
    for match_id, players in rosters.items():
        allowed = {p['puuid'] for p in players}
        observed = {key: value for key, value in rows[match_id].items()
                    if key in allowed and value['queue_id'] == players[0]['queue_id']}
        scores = [score for row in observed.values() if (score := rank_score(row)) is not None]
        stamps = [float(row['fetched_at']) for row in observed.values()
                  if isinstance(row['fetched_at'], (int, float)) and 0 <= row['fetched_at'] <= 253370764800]
        own = observed.get(owner)
        if own and own['status'] == 'ranked' and rank_score(own) is None:
            own = {'status': 'unavailable'}
        unranked = sum(row['status'] == 'unranked' for row in observed.values())
        distribution = defaultdict(int)
        for row in observed.values():
            if rank_score(row) is not None:
                distribution[LABELS[row['tier']]] += 1
        result[match_id] = RankContext(match_id, players[0]['queue_id'], len(allowed), len(observed), len(scores),
                                      rank_label(own), average_label(scores), min(stamps) if stamps else None,
                                      max(stamps) if stamps else None,
                                      average_label([median(scores)]) if scores else 'Indisponible',
                                      average_label([min(scores)]) if scores else 'Indisponible',
                                      average_label([max(scores)]) if scores else 'Indisponible',
                                      unranked, len(observed) - len(scores) - unranked,
                                      len(allowed) - len(observed),
                                      tuple((LABELS[tier], distribution[LABELS[tier]]) for tier in TIERS + APEX if distribution[LABELS[tier]]))
    return result


def capture_rank_snapshot(database: Database, api, match_id: str, owner: str, *, progress=lambda current, total: None, clock=time.time):
    """Collect each participant once; keep successful observations on interruption.

    A later retry completes missing rows without rewriting earlier snapshots.
    No claim is made about the player's rank when the match itself was played.
    """
    context = load_rank_contexts(database, owner, [match_id]).get(match_id)
    if context is None or not 1 <= context.total <= 10:
        raise ValueError('Partie Ranked locale introuvable pour ce profil ou composition invalide.')
    with database.connection() as connection:
        participants = [row[0] for row in connection.execute('SELECT puuid FROM participants WHERE match_id=? ORDER BY puuid', (match_id,))]
        saved = {row[0] for row in connection.execute('SELECT puuid FROM rank_snapshots WHERE match_id=?', (match_id,))}
    checked = len(saved.intersection(participants))
    progress(checked, len(participants))
    for puuid in participants:
        if puuid in saved:
            continue
        try:
            observation = rank_entry(api.get_league_entries(puuid), context.queue_id)
        except RiotNotFoundError:
            observation = {'status': 'unavailable', 'tier': None, 'division': None, 'league_points': None}
        stamp = clock()
        if not math.isfinite(stamp) or not 0 <= stamp <= 253370764800:
            raise ValueError('Horloge locale invalide.')
        with database.connection() as connection:
            connection.execute('''INSERT OR IGNORE INTO rank_snapshots
                (match_id, puuid, queue_id, status, tier, division, league_points, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (match_id, puuid, context.queue_id, observation['status'], observation['tier'],
                 observation['division'], observation['league_points'], stamp))
        checked += 1
        progress(checked, len(participants))
    return load_rank_contexts(database, owner, [match_id])[match_id]
