"""Immutable personal observations, separate from match-owned lobby snapshots."""
import math
import time

from core.db import Database
from core.exceptions import RiotNotFoundError
from core.profiles import routing_for_platform
from core.ranks import RANKED_QUEUES, rank_entry, rank_score


def load_profile_ranks(database: Database, owner: str, platform: str, queue_id: int) -> list[dict]:
    """Local-only read with defensive validation of legacy/manual rows."""
    routing_for_platform(platform)
    if queue_id not in RANKED_QUEUES:
        raise ValueError('File classée non prise en charge.')
    with database.connection() as connection:
        rows = connection.execute('''SELECT * FROM profile_rank_observations
            WHERE owner_puuid=? AND platform_region=? AND queue_id=? ORDER BY observed_at''',
            (owner, platform.upper(), queue_id)).fetchall()
    result = []
    for raw in rows:
        row = dict(raw)
        stamp = row['observed_at']
        if type(stamp) not in (float, int) or not math.isfinite(stamp) or not 0 <= stamp <= 253370764800:
            continue
        if row['status'] == 'ranked' and rank_score(row) is None:
            row.update(status='unavailable', tier=None, division=None, league_points=None)
        result.append(row)
    return result


def capture_profile_rank(database: Database, api, owner: str, platform: str, queue_id: int,
                         job_id: str, *, clock=time.time) -> dict:
    """One intentional League request; retry after commit never re-queries Riot.

    Caller holds the library ImportLock and supplies the guarded JobAPI.
    A network/auth failure propagates without being written as "unranked".
    """
    platform = platform.upper()
    routing_for_platform(platform)
    if queue_id not in RANKED_QUEUES or not database.get_player(owner):
        raise ValueError('Profil enregistré et file classée nécessaires.')
    with database.connection() as connection:
        job = connection.execute('SELECT * FROM import_jobs WHERE job_id=?', (job_id,)).fetchone()
        if (not job or job['puuid'] != owner or job['kind'] != 'profile_rank'
                or job['target'] != queue_id or job['platform_region'].upper() != platform):
            raise ValueError('Observation incompatible avec le job demandé.')
        saved = connection.execute('SELECT * FROM profile_rank_observations WHERE job_id=?', (job_id,)).fetchone()
    if saved:
        if saved['owner_puuid'] != owner or saved['platform_region'] != platform or saved['queue_id'] != queue_id:
            raise ValueError('Observation enregistrée incohérente avec son job.')
        return dict(saved)
    try:
        observation = rank_entry(api.get_league_entries(owner), queue_id)
    except RiotNotFoundError:
        observation = {'status': 'unavailable', 'tier': None, 'division': None, 'league_points': None}
    stamp = clock()
    if type(stamp) not in (float, int) or not math.isfinite(stamp) or not 0 <= stamp <= 253370764800:
        raise ValueError('Horloge locale invalide.')
    with database.connection() as connection:
        # Conflict is explicit, never overwrite an earlier observation or create
        # a fictional timestamp to force a second point at the same clock tick.
        connection.execute('''INSERT INTO profile_rank_observations
            (owner_puuid, platform_region, queue_id, observed_at, status, tier, division, league_points, job_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (owner, platform, queue_id, stamp, observation['status'], observation['tier'],
             observation['division'], observation['league_points'], job_id))
    return {'owner_puuid': owner, 'platform_region': platform, 'queue_id': queue_id,
            'observed_at': stamp, 'source': 'league-v4', 'job_id': job_id, **observation}
