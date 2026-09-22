"""Offline jobs: repeated 429s, cancellation, restart and exact saved progress."""
from copy import deepcopy
from functools import partial
import json
import math
import sqlite3
import subprocess
import sys

import httpx
import pytest

from config.settings import Settings
from core.db import Database
from core.import_jobs import JobStore, execute_job, JobAPI
from core.import_lock import ImportLock, ImportBusyError, import_is_running
from core.models import RiotAccount
from core.riot_api import RiotAPIClient
from core.exceptions import RiotRateLimitError


@pytest.mark.parametrize('operation', ['profile', 'sync', 'backfill', 'timeline', 'identities'])
@pytest.mark.parametrize('barrier', ['active_worker', 'persisted_cooldown'])
def test_other_explicit_actions_cannot_bypass_worker_or_cancelled_job_cooldown(database, monkeypatch, operation, barrier):
    from core.profiles import import_profile
    from core.sync import sync_from_settings, backfill_from_settings
    from core.timeline import enrich_timelines_from_settings
    from core.identities import hydrate_identities
    settings = Settings(database_path=database.path, riot_api_key='synthetic-guard-secret',
                        riot_game_name='Job Player', riot_tag_line='TEST')
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('No request may bypass the library barrier')
    monkeypatch.setattr(httpx.Client, 'send', forbidden)
    actions = {
        'profile': lambda: import_profile(settings, 'Job Player#TEST', 'EUW1'),
        'sync': lambda: sync_from_settings(settings),
        'backfill': lambda: backfill_from_settings(settings, 500),
        'timeline': lambda: enrich_timelines_from_settings(settings, 'player-puuid'),
        'identities': lambda: hydrate_identities(settings, 'player-puuid'),
    }
    lock = ImportLock(database.path).__enter__() if barrier == 'active_worker' else None
    if barrier == 'persisted_cooldown':
        JobStore(database).postpone(120)
    try:
        with pytest.raises(ImportBusyError, match='en cours|Limite Riot encore active'):
            actions[operation]()
    finally:
        if lock:
            lock.__exit__()
    assert calls == []
    assert not import_is_running(database.path)


def test_legacy_cooldown_guard_reads_without_creating_or_migrating_a_database(tmp_path):
    from core.import_lock import ensure_riot_ready
    path = tmp_path / 'absent.db'
    ensure_riot_ready(path)
    assert not path.exists()
    connection = sqlite3.connect(path)
    connection.execute('CREATE TABLE untouched(value TEXT)')
    connection.close()
    ensure_riot_ready(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('untouched',)]


def test_legacy_cooldown_guard_releases_at_deadline_and_rejects_nonfinite(database):
    from core.import_lock import ensure_riot_ready
    store = JobStore(database, lambda: 1000)
    store.postpone(120)
    with pytest.raises(ImportBusyError, match='attendez 1 s'):
        ensure_riot_ready(database.path, clock=lambda: 1119.5)
    ensure_riot_ready(database.path, clock=lambda: 1120)
    with database.connection() as connection:
        connection.execute('UPDATE import_cooldown SET not_before=?', (math.inf,))
    with pytest.raises(ImportBusyError, match='invalide'):
        ensure_riot_ready(database.path, clock=lambda: 1121)


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def setup_job(database, count=500):
    settings = Settings(database_path=database.path, riot_api_key='synthetic-job-secret',
                        riot_game_name='Job Player', riot_tag_line='TEST')
    clock = Clock()
    store = JobStore(database, clock)
    job_id = store.create(settings, 'backfill', count)
    return settings, clock, store, job_id


def factory(handler):
    return partial(RiotAPIClient, transport=httpx.MockTransport(handler))


def run(settings, store, job_id, handler, clock, wait=None):
    with ImportLock(store.database.path):
        execute_job(settings, store, job_id, client_factory=factory(handler),
                    wait=wait or clock.sleep, monotonic=clock)


class RiotFixture:
    def __init__(self, payload, clock, count=500):
        self.payload, self.clock, self.count = payload, clock, count
        self.calls = []
        self.ids = [f'EUW1_JOB_{i}' for i in range(count)]
        self.throttled = set()
        self.throttle_at = {85, 170, 255, 340, 425}
        self.delay = 120

    def __call__(self, request):
        path = request.url.path
        self.calls.append((self.clock(), path, str(request.url.query)))
        if '/account/' in path:
            return httpx.Response(200, json={'puuid': 'player-puuid', 'gameName': 'Job Player', 'tagLine': 'TEST'})
        if path.endswith('/ids'):
            start, count = int(request.url.params['start']), int(request.url.params['count'])
            return httpx.Response(200, json=self.ids[start:start+count])
        match_id = path.rsplit('/', 1)[1]
        index = self.ids.index(match_id)
        if index in self.throttle_at and index not in self.throttled:
            self.throttled.add(index)
            return httpx.Response(429, headers={'Retry-After': str(self.delay)})
        payload = deepcopy(self.payload)
        payload['metadata']['matchId'] = match_id
        return httpx.Response(200, json=payload)


def test_500_matches_many_rate_limits_complete_without_reclick_or_duplicate(database, riot_match_payload):
    settings, clock, store, job_id = setup_job(database)
    riot = RiotFixture(riot_match_payload, clock)
    run(settings, store, job_id, riot, clock)
    job = store.get(job_id)
    assert job['status'] == 'completed' and job['local_count'] == 500
    assert database.count_player_matches('player-puuid') == 500
    assert database.get_sync_state('player-puuid') is None  # historical backfill does not advance incremental anchor
    for index in riot.throttle_at:
        calls = [stamp for stamp, path, _ in riot.calls if path.endswith('/' + riot.ids[index])]
        assert len(calls) == 2 and calls[1] - calls[0] >= 120
    with database.connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM matches').fetchone()[0] == 500
        assert connection.execute('SELECT COUNT(*) FROM participants').fetchone()[0] == 2000
        assert not connection.execute('PRAGMA foreign_key_check').fetchall()
        assert 'synthetic-job-secret' not in '\n'.join(connection.iterdump())
    assert max(clock.sleeps) <= 0.25


def test_cancel_while_waiting_keeps_85_games_and_cooldown_for_next_job(database, riot_match_payload):
    settings, clock, store, job_id = setup_job(database)
    riot = RiotFixture(riot_match_payload, clock)
    def cancel(seconds):
        assert store.get(job_id)['status'] == 'rate_limited'
        assert store.get(job_id)['local_count'] == 85
        store.cancel(job_id)
        clock.sleep(seconds)
    run(settings, store, job_id, riot, clock, wait=cancel)
    assert store.get(job_id)['status'] == 'cancelled'
    assert database.count_player_matches('player-puuid') == 85
    assert store.cooldown() > clock()
    deadline = store.cooldown()
    second_job = store.create(settings, 'backfill', 100)
    previous = len(riot.calls)
    run(settings, store, second_job, riot, clock)
    assert riot.calls[previous][0] >= deadline
    assert store.get(second_job)['local_count'] == 100


def test_restart_mid_rate_limit_reuses_persisted_pages_and_skips_committed_details(database, riot_match_payload):
    settings, clock, store, job_id = setup_job(database, 100)
    riot = RiotFixture(riot_match_payload, clock, count=100)
    def power_off(seconds):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        run(settings, store, job_id, riot, clock, wait=power_off)
    assert store.get(job_id)['status'] == 'rate_limited'
    assert database.count_player_matches('player-puuid') == 85
    assert not import_is_running(database.path)
    restarted_store = JobStore(Database(database.path), clock)
    previous = len(riot.calls)
    deadline = store.cooldown()
    run(settings, restarted_store, job_id, riot, clock)
    new = riot.calls[previous:]
    assert new[0][0] >= deadline
    assert not any(path.endswith('/ids') for _, path, _ in new)
    assert not any(path.endswith('/' + riot.ids[i]) for _, path, _ in new for i in range(85))
    assert restarted_store.get(job_id)['status'] == 'completed'
    assert database.count_player_matches('player-puuid') == 100


def test_bad_auth_pauses_and_keeps_safe_message(database, riot_match_payload):
    settings, clock, store, job_id = setup_job(database, 100)
    run(settings, store, job_id, lambda _: httpx.Response(403, json={'error': settings.riot_api_key}), clock)
    job = store.get(job_id)
    assert job['status'] == 'paused' and 'expirée' in job['message']
    assert settings.riot_api_key not in json.dumps(job)
    assert not clock.sleeps


@pytest.mark.parametrize('header,expected', [('120', 120), ('0', 0), (None, 120), ('-1', None), ('NaN', None), ('Infinity', None), ('999999999999', None), ('nonsense', None)])
def test_deferred_retry_after_parsing_never_sleeps_in_client(header, expected):
    waits = []
    response = httpx.Response(429, headers={} if header is None else {'Retry-After': header})
    with RiotAPIClient('test', defer_rate_limits=True, sleep=waits.append,
                       transport=httpx.MockTransport(lambda _: response)) as client:
        with pytest.raises(RiotRateLimitError) as error:
            client.get_match_ids('p')
    assert error.value.retry_after == expected and waits == []


def test_http_date_retry_after(monkeypatch):
    from email.utils import formatdate
    monkeypatch.setattr('core.riot_api.time.time', lambda: 1000)
    response = httpx.Response(429, headers={'Retry-After': formatdate(1120, usegmt=True)})
    assert RiotAPIClient._deferred_retry_after(response) == 120


def test_cancel_flag_cannot_be_lost_to_concurrent_progress(database):
    _, clock, store, job_id = setup_job(database)
    store.cancel(job_id)
    store.update(job_id, status='running', local_count=85)
    assert store.get(job_id)['status'] == 'cancel_requested'
    store.update(job_id, status='completed', completed=100)
    assert store.get(job_id)['status'] == 'cancel_requested'


def test_import_lock_prevents_cleanup_and_other_worker(database):
    database.upsert_player(RiotAccount(puuid='main', gameName='Main', tagLine='TEST'))
    database.upsert_player(RiotAccount(puuid='secondary', gameName='Secondary', tagLine='TEST'))
    with ImportLock(database.path):
        assert import_is_running(database.path)
        with pytest.raises(ImportBusyError):
            with ImportLock(database.path):
                pass
        with pytest.raises(ImportBusyError):
            database.remove_library_profile('secondary', 'Main', 'TEST')
    assert database.get_player('secondary')
    assert not import_is_running(database.path)


def test_cleanup_cascades_job_metadata_and_cached_pages(database):
    settings, clock, store, _ = setup_job(database)
    database.upsert_player(RiotAccount(puuid='main', gameName='Main', tagLine='TEST'))
    database.upsert_player(RiotAccount(puuid='secondary', gameName='Secondary', tagLine='TEST'))
    secondary = settings.model_copy(update={'riot_game_name': 'Secondary'})
    job_id = store.create(secondary, 'backfill', 500)
    store.save_page(job_id, 'key', ['EUW1_SYNTHETIC'])
    assert database.remove_library_profile('secondary', 'Main', 'TEST')
    assert store.get(job_id) is None
    assert store.page(job_id, 'key') is None


def test_import_lock_is_cross_process(database):
    code = '''
import sys
from core.import_lock import ImportLock, ImportBusyError
try:
    with ImportLock(sys.argv[1]):
        pass
except ImportBusyError:
    raise SystemExit(42)
'''
    command = [sys.executable, '-c', code, str(database.path)]
    with ImportLock(database.path):
        assert subprocess.run(command, capture_output=True).returncode == 42
    assert subprocess.run(command, capture_output=True).returncode == 0


def test_pinned_profile_cannot_be_reassigned_on_resume(database, riot_match_payload):
    settings, clock, store, job_id = setup_job(database, 100)
    database.upsert_player(RiotAccount(puuid='original-owner', gameName='Job Player', tagLine='TEST'))
    store.update(job_id, puuid='original-owner')
    riot = RiotFixture(riot_match_payload, clock, count=100)
    run(settings, store, job_id, riot, clock)
    assert store.get(job_id)['status'] == 'paused'
    assert len(riot.calls) == 1
    assert database.count_player_matches('player-puuid') == 0
    assert not database.get_player('player-puuid')


def test_sidebar_job_ui_shows_wait_and_cancel_without_blocking(database):
    from streamlit.testing.v1 import AppTest
    settings, clock, store, job_id = setup_job(database)
    # Use real wall time for the visible countdown, independent of the test clock.
    import time
    real_store = JobStore(database)
    real_store.postpone(42)
    store.update(job_id, status='rate_limited', local_count=85)
    source = '''
import streamlit as st
from ui.import_jobs import render_import_status
render_import_status(st.session_state['settings'], None)
'''
    app = AppTest.from_string(source)
    app.session_state['settings'] = settings
    with ImportLock(database.path):
        app.run(timeout=10)
        assert not app.exception
        assert any('Limite Riot atteinte' in info.value for info in app.info)
        assert '85 / 500' in app.get('progress')[0].proto.text
        app.button(key='library_job_cancel_' + job_id).click().run(timeout=10)
        assert not app.exception
        assert store.get(job_id)['status'] == 'cancel_requested'


def test_sidebar_restart_offers_resume_without_automatic_network(database, monkeypatch):
    from streamlit.testing.v1 import AppTest
    settings, _, store, job_id = setup_job(database)
    store.update(job_id, status='running', local_count=85)
    calls = []
    monkeypatch.setattr('ui.import_jobs.resume_import_job', lambda settings, identifier: calls.append(identifier))
    app = AppTest.from_string('''
import streamlit as st
from ui.import_jobs import render_import_status
render_import_status(st.session_state['settings'], None)
''')
    app.session_state['settings'] = settings
    app.run(timeout=10)
    assert not app.exception and not calls
    app.button(key='library_job_resume_' + job_id).click().run(timeout=10)
    assert not app.exception and calls == [job_id]
