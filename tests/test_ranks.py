from copy import deepcopy
from functools import partial
import math

import httpx
import pytest

from config.settings import Settings
from core.db import Database
from core.exceptions import RiotAPIError, RiotRateLimitError
from core.import_jobs import JobStore, execute_job
from core.import_lock import ImportLock
from core.models import RiotAccount, parse_riot_match
from core.ranks import rank_entry, rank_score, average_label, capture_rank_snapshot, load_rank_contexts
from core.riot_api import RiotAPIClient
from tests.test_import_jobs import Clock


def entry(tier='GOLD', rank='IV', lp=43, queue='RANKED_SOLO_5x5'):
    return {'tier': tier, 'rank': rank, 'leaguePoints': lp, 'queueType': queue}


@pytest.mark.parametrize('queue', [400, 450, 490, 700, None])
def test_only_relevant_ranked_queues_are_eligible(queue):
    with pytest.raises(ValueError):
        rank_entry([entry()], queue)


def test_queue_specific_rank_is_selected_and_unranked_not_zero():
    rows = [entry('SILVER', 'II', 10), entry('PLATINUM', 'I', 80, queue='RANKED_FLEX_SR')]
    assert rank_entry(rows, 420)['tier'] == 'SILVER'
    assert rank_entry(rows, 440)['tier'] == 'PLATINUM'
    unranked = rank_entry([], 420)
    assert unranked['status'] == 'unranked' and rank_score(unranked) is None


@pytest.mark.parametrize('tier,division,lp', [('FUTURE', 'I', 4), ('GOLD', '?', 4), ('GOLD', 'I', None), ('GOLD', 'I', math.inf), ('GOLD', 'I', True), ('GOLD', 'I', -5), ('GOLD', 'I', 10000)])
def test_unknown_or_malformed_rank_unavailable(tier, division, lp):
    assert rank_entry([entry(tier, division, lp)], 420)['status'] == 'unavailable'


def test_ambiguous_entries_are_rejected():
    with pytest.raises(RiotAPIError):
        rank_entry([entry(), entry()], 420)


def test_average_division_lp_and_apex_are_descriptive():
    scores = [rank_score(rank_entry([entry('SILVER', 'II', 0)], 420)),
              rank_score(rank_entry([entry('GOLD', 'IV', 0)], 420))]
    assert average_label(scores) == 'Silver I · ~0 LP'
    assert average_label([1543, 1543]) == 'Gold I · ~43 LP'
    assert average_label([]) == 'Indisponible'
    assert average_label([3000, 4000]) == 'Master+ · ~700 LP'


def test_league_by_puuid_uses_platform_endpoint_and_rejects_other_identity():
    urls = []
    def handler(request):
        urls.append(str(request.url))
        return httpx.Response(200, json=[dict(entry(), puuid='other')])
    with RiotAPIClient('test', platform_region='NA1', transport=httpx.MockTransport(handler)) as api:
        with pytest.raises(RiotAPIError, match='identity'):
            api.get_league_entries('requested/p')
    assert urls == ['https://na1.api.riotgames.com/lol/league/v4/entries/by-puuid/requested%2Fp']


@pytest.fixture
def rank_library(database, riot_match_payload):
    database.insert_match(parse_riot_match(riot_match_payload))
    database.upsert_player(RiotAccount(puuid='player-puuid', gameName='Rank Player', tagLine='TEST'), 'EUW1', 'EUROPE')
    return database, 'EUW1_123456'


def test_snapshot_is_immutable_partial_and_profile_scoped(rank_library):
    database, match_id = rank_library
    calls = []
    class API:
        def get_league_entries(self, puuid):
            calls.append(puuid)
            return [] if puuid == 'red-two' else [entry('SILVER' if puuid == 'player-puuid' else 'GOLD')]
    before = database.player_matches('player-puuid')
    context = capture_rank_snapshot(database, API(), match_id, 'player-puuid', clock=lambda: 1800000000)
    assert context.total == context.observed == 4 and context.ranked == 3
    assert context.own_label == 'Silver IV · 43 LP'
    assert context.first_observed == context.last_observed == 1800000000
    assert database.player_matches('player-puuid') == before
    assert load_rank_contexts(database, 'not-a-participant', [match_id]) == {}
    with database.connection() as connection:
        saved = [tuple(row) for row in connection.execute('SELECT * FROM rank_snapshots ORDER BY puuid')]
    capture_rank_snapshot(database, API(), match_id, 'player-puuid', clock=lambda: 1900000000)
    assert len(calls) == 4
    with database.connection() as connection:
        assert [tuple(row) for row in connection.execute('SELECT * FROM rank_snapshots ORDER BY puuid')] == saved


def test_partial_snapshot_resumes_without_replacing_earlier_observations(rank_library):
    database, match_id = rank_library
    calls = []
    class FirstAPI:
        def get_league_entries(self, puuid):
            calls.append(puuid)
            if len(calls) == 3:
                raise RiotRateLimitError('pause')
            return [entry()]
    with pytest.raises(RiotRateLimitError):
        capture_rank_snapshot(database, FirstAPI(), match_id, 'player-puuid', clock=lambda: 1800000000)
    assert load_rank_contexts(database, 'player-puuid', [match_id])[match_id].observed == 2
    second_calls = []
    class SecondAPI:
        def get_league_entries(self, puuid):
            second_calls.append(puuid)
            return [entry('SILVER')]
    context = capture_rank_snapshot(database, SecondAPI(), match_id, 'player-puuid', clock=lambda: 1800000600)
    assert len(second_calls) == 2 and not set(second_calls).intersection(calls[:2])
    assert context.first_observed == 1800000000 and context.last_observed == 1800000600


def test_rank_job_waits_429_then_finishes_without_match_import(rank_library):
    database, match_id = rank_library
    settings = Settings(database_path=database.path, riot_api_key='synthetic', riot_game_name='Rank Player', riot_tag_line='TEST')
    clock = Clock()
    store = JobStore(database, clock)
    job_id = store.create(settings, 'ranks', None, match_id=match_id, owner='player-puuid')
    calls = []
    def handler(request):
        calls.append((clock(), request.url.path))
        assert '/league/v4/entries/by-puuid/' in request.url.path
        if len(calls) == 2:
            return httpx.Response(429, headers={'Retry-After': '42'})
        return httpx.Response(200, json=[entry()])
    with ImportLock(database.path):
        execute_job(settings, store, job_id, client_factory=partial(RiotAPIClient, transport=httpx.MockTransport(handler)), wait=clock.sleep, monotonic=clock)
    assert store.get(job_id)['status'] == 'completed'
    assert store.get(job_id)['completed'] == store.get(job_id)['total'] == 4
    assert calls[2][0] - calls[1][0] >= 42 and calls[2][1] == calls[1][1]
    assert database.count_player_matches('player-puuid') == 1


def test_rank_rows_cascade_with_exclusive_match_only(rank_library):
    database, match_id = rank_library
    class API:
        def get_league_entries(self, puuid):
            return [entry()]
    capture_rank_snapshot(database, API(), match_id, 'player-puuid')
    database.upsert_player(RiotAccount(puuid='main-with-no-games', gameName='Main', tagLine='TEST'))
    result = database.remove_library_profile('player-puuid', 'Main', 'TEST')
    assert result.deleted_matches == 1
    with database.connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM rank_snapshots').fetchone()[0] == 0
        assert not connection.execute('PRAGMA foreign_key_check').fetchall()


def test_rank_panel_is_explicit_and_labels_date(rank_library, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from core.ranks import RankContext
    context = load_rank_contexts(rank_library[0], 'player-puuid', [rank_library[1]])[rank_library[1]]
    calls = []
    monkeypatch.setattr('ui.ranks.start_import_job', lambda *args, **kwargs: calls.append(kwargs))
    app = AppTest.from_string('''
import streamlit as st
from ui.ranks import render_rank_panel
render_rank_panel(st.session_state['context'], st.session_state['settings'], 'player-puuid')
''')
    app.session_state['context'] = context
    app.session_state['settings'] = Settings(database_path=rank_library[0].path, riot_api_key='synthetic')
    app.run(timeout=10)
    assert not app.exception and not calls
    assert any('ne reconstitue pas les rangs anciens' in text.value for text in app.caption)
    app.button(key='history_rank_enrich_' + rank_library[1]).click().run(timeout=10)
    assert not app.exception and calls == [{'match_id': rank_library[1], 'owner': 'player-puuid'}]
