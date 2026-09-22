from copy import deepcopy
from types import SimpleNamespace

import pytest

from core.identities import IdentityHydrationService, local_identity_index, valid_riot_identity
from core.models import parse_riot_match


def snapshot(database):
    with database.connection() as c:
        columns = [r[1] for r in c.execute('PRAGMA table_info(participants)') if r[1] not in ('teammate_game_name', 'teammate_tag_line')]
        return {t: c.execute('SELECT ' + (','.join(columns) if t == 'participants' else '*') + ' FROM ' + t).fetchall()
                for t in ('players', 'matches', 'participants', 'sync_state', 'timeline_status', 'timeline_frames', 'timeline_events')}


@pytest.mark.parametrize('name,tag', [(None, 'TAG'), ('None', 'TAG'), ('', ''), ('Player', 'null'), ('A\n', 'TAG'), ('A#B', 'TAG')])
def test_invalid_display_pair(name, tag):
    assert valid_riot_identity(name, tag) is None


def test_rename_uses_latest_known_not_order_or_null():
    rows = [dict(puuid='mate', match_id=str(i), game_creation=i, teammate_game_name=n, teammate_tag_line='TAG')
            for i, n in [(3, None), (2, 'Nouveau'), (1, 'Ancien')]]
    identity = local_identity_index(rows)['mate']
    assert identity.riot_id == 'Nouveau#TAG'
    assert identity.previous_names == ('Ancien#TAG',)
    assert local_identity_index(list(reversed(rows))) == local_identity_index(rows)


def test_hydration_deduplicates_and_never_changes_statistics(database, riot_match_payload, second_riot_match_payload):
    for payload in (riot_match_payload, second_riot_match_payload):
        database.insert_match(parse_riot_match(payload))
    before = snapshot(database)
    calls, reports = [], []
    def fetch(match_id):
        calls.append(match_id)
        result = deepcopy(second_riot_match_payload)
        result['metadata']['matchId'] = match_id
        for i, row in enumerate(result['info']['participants']):
            row.update(riotIdGameName=f'Synthetic {i}', riotIdTagline='TEST', kills=999999)
        result['info']['participants'].append(dict(puuid='foreign', riotIdGameName='Not inserted', riotIdTagline='TEST'))
        return result
    service = IdentityHydrationService(database, SimpleNamespace(get_match=fetch))
    result = service.run('player-puuid', reports.append)
    assert calls == ['EUW1_123457']
    assert result.recovered == 4 and result.skipped == 1 and result.remaining == 0
    assert snapshot(database) == before
    assert service.run('player-puuid').checked == 0
    assert len(calls) == 1
    database.initialize(); database.initialize()
    assert snapshot(database) == before


def test_missing_fields_checkpoint_and_explicit_retry(database, riot_match_payload):
    database.insert_match(parse_riot_match(riot_match_payload))
    calls = []
    def fetch(key):
        calls.append(key)
        return riot_match_payload
    service = IdentityHydrationService(database, SimpleNamespace(get_match=fetch))
    assert service.run('player-puuid').remaining == 4
    assert service.run('player-puuid').checked == 0
    assert service.run('player-puuid', retry_unavailable=True).checked == 1
    assert len(calls) == 2


@pytest.mark.parametrize('failure', [TimeoutError(), KeyboardInterrupt(), ValueError('invalid'), RuntimeError('429')])
def test_failure_is_resumable(database, riot_match_payload, failure):
    database.insert_match(parse_riot_match(riot_match_payload))
    before = snapshot(database)
    def fetch(key):
        raise failure
    with pytest.raises(type(failure)):
        IdentityHydrationService(database, SimpleNamespace(get_match=fetch)).run('player-puuid')
    assert snapshot(database) == before
    with database.connection() as c:
        assert c.execute('SELECT COUNT(*) FROM identity_fetch_status').fetchone()[0] == 0
    assert IdentityHydrationService(database, SimpleNamespace(get_match=lambda key: riot_match_payload)).run('player-puuid').checked == 1


@pytest.mark.parametrize('invalid', ['match', 'owner', 'duplicate', 'empty', 'null', 'partial'])
def test_wrong_payload_rejected_without_checkpoint(database, riot_match_payload, invalid):
    database.insert_match(parse_riot_match(riot_match_payload))
    payload = deepcopy(riot_match_payload)
    if invalid == 'match': payload['metadata']['matchId'] = 'foreign'
    if invalid == 'owner': payload['info']['participants'][0]['puuid'] = 'foreign'
    if invalid == 'duplicate': payload['info']['participants'].append(payload['info']['participants'][0])
    if invalid == 'empty': payload['info']['participants'] = []
    if invalid == 'partial': payload['info']['participants'] = payload['info']['participants'][:2]
    if invalid == 'null': payload = None
    before = snapshot(database)
    with pytest.raises(ValueError):
        IdentityHydrationService(database, SimpleNamespace(get_match=lambda key: payload)).run('player-puuid')
    assert snapshot(database) == before
    with database.connection() as c:
        assert c.execute('SELECT COUNT(*) FROM identity_fetch_status').fetchone()[0] == 0


def test_hydration_actual_client_obeys_429_and_updates_identity_only(database, riot_match_payload):
    import httpx
    from core.riot_api import RiotAPIClient
    database.insert_match(parse_riot_match(riot_match_payload))
    before, calls, waits = snapshot(database), [], []
    payload = deepcopy(riot_match_payload)
    for i, row in enumerate(payload['info']['participants']):
        row.update(riotIdGameName=f'Synthetic Recovered {i}', riotIdTagline='TEST')
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(429, headers={'Retry-After': '2'}) if len(calls) == 1 else httpx.Response(200, json=payload)
    with RiotAPIClient('test-key', transport=httpx.MockTransport(handler), sleep=waits.append) as api:
        result = IdentityHydrationService(database, api).run('player-puuid')
    assert result.recovered == 4 and waits == [2]
    assert len(calls) == 2 and all(path.endswith('/matches/EUW1_123456') for path in calls)
    assert snapshot(database) == before


def test_hydration_interrupt_after_commit_resumes_without_duplicate_detail(database, riot_match_payload, second_riot_match_payload):
    for p in (riot_match_payload, second_riot_match_payload): database.insert_match(parse_riot_match(p))
    calls = []
    def fetch(key):
        calls.append(key)
        return second_riot_match_payload if key.endswith('7') else riot_match_payload
    service = IdentityHydrationService(database, SimpleNamespace(get_match=fetch))
    def interrupt(progress):
        if progress.checked == 1: raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt): service.run('player-puuid', interrupt)
    assert service.run('player-puuid').checked == 1
    assert calls == ['EUW1_123457', 'EUW1_123456']
