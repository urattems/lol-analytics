"""Intentional, immutable profile ranks share the cancellable import guard."""
import sqlite3

import pytest

from config.settings import Settings
from core.exceptions import RiotAuthenticationError, RiotNetworkError, RiotRateLimitError
from core.import_jobs import JobStore, execute_job
from core.models import RiotAccount
from core.profile_ranks import capture_profile_rank, load_profile_ranks
from tests.test_ranks import entry


@pytest.fixture
def personal_rank(database):
    database.upsert_player(RiotAccount(puuid='rank-owner', gameName='Rank Owner', tagLine='TEST'), 'EUW1', 'EUROPE')
    settings = Settings(database_path=database.path, riot_api_key='synthetic', riot_game_name='Rank Owner', riot_tag_line='TEST')
    store = JobStore(database, clock=lambda: 1_780_000_000.0)
    return database, settings, store


def job_for(fixture, queue=420):
    database, settings, store = fixture
    return store.create(settings, 'profile_rank', queue, owner='rank-owner')


def test_success_is_dated_queue_scoped_and_immutable_on_resume(personal_rank):
    database, _, store = personal_rank
    job = job_for(personal_rank)
    calls = []
    class API:
        def get_league_entries(self, owner):
            calls.append(owner)
            return [entry('SILVER', 'II', 43), entry('GOLD', 'IV', 12, 'RANKED_FLEX_SR')]
    saved = capture_profile_rank(database, API(), 'rank-owner', 'EUW1', 420, job, clock=store.clock)
    capture_profile_rank(database, API(), 'rank-owner', 'EUW1', 420, job, clock=lambda: 1_900_000_000)
    rows = load_profile_ranks(database, 'rank-owner', 'EUW1', 420)
    assert calls == ['rank-owner'] and len(rows) == 1 and rows[0]['observed_at'] == 1_780_000_000
    assert saved['division'] == 'II' and saved['league_points'] == 43
    assert load_profile_ranks(database, 'other', 'EUW1', 420) == []
    assert load_profile_ranks(database, 'rank-owner', 'NA1', 420) == []
    assert load_profile_ranks(database, 'rank-owner', 'EUW1', 440) == []
    store.update(job, status='completed')
    second = job_for(personal_rank)
    capture_profile_rank(database, API(), 'rank-owner', 'EUW1', 420, second, clock=lambda: 1_780_086_400)
    assert len(load_profile_ranks(database, 'rank-owner', 'EUW1', 420)) == 2


def test_network_failure_does_not_record_unranked(personal_rank):
    database, _, store = personal_rank
    job = job_for(personal_rank)
    class API:
        def get_league_entries(self, owner):
            raise RiotNetworkError('synthetic failure')
    with pytest.raises(RiotNetworkError):
        capture_profile_rank(database, API(), 'rank-owner', 'EUW1', 420, job, clock=store.clock)
    assert load_profile_ranks(database, 'rank-owner', 'EUW1', 420) == []
    class Unranked:
        def get_league_entries(self, owner):
            return []
    row = capture_profile_rank(database, Unranked(), 'rank-owner', 'EUW1', 420, job, clock=store.clock)
    assert row['status'] == 'unranked' and row['league_points'] is None


@pytest.mark.parametrize('stamp', [float('inf'), float('nan'), -1, True, 10**20])
def test_invalid_capture_clock_never_creates_a_point(personal_rank, stamp):
    database, _, _ = personal_rank
    job = job_for(personal_rank)
    class API:
        def get_league_entries(self, owner):
            return [entry()]
    with pytest.raises(ValueError):
        capture_profile_rank(database, API(), 'rank-owner', 'EUW1', 420, job, clock=lambda: stamp)
    assert load_profile_ranks(database, 'rank-owner', 'EUW1', 420) == []


def test_wrong_job_owner_queue_or_region_rejected_before_request(personal_rank):
    database, _, _ = personal_rank
    job = job_for(personal_rank)
    class API:
        def get_league_entries(self, owner):
            pytest.fail('wrong scope sent request')
    for owner, region, queue in [('other', 'EUW1', 420), ('rank-owner', 'NA1', 420), ('rank-owner', 'EUW1', 440)]:
        with pytest.raises(ValueError):
            capture_profile_rank(database, API(), owner, region, queue, job)


def test_rank_job_respects_cooldown_retries_exact_owner_and_resumes_after_commit(personal_rank):
    database, settings, store = personal_rank
    from tests.test_import_jobs import Clock
    clock = Clock()
    store.clock = clock
    calls = []
    class API:
        def __init__(self, *a, **kw):
            assert kw['defer_rate_limits'] is True
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get_league_entries(self, owner):
            calls.append((owner, clock()))
            if len(calls) == 1:
                raise RiotRateLimitError('synthetic', retry_after=2)
            return [entry()]
    job = job_for(personal_rank)
    store.postpone(3)
    started = clock()
    execute_job(settings, store, job, client_factory=API, wait=clock.sleep, monotonic=clock)
    assert store.get(job)['status'] == 'completed'
    assert len(calls) == 2 and calls[0][1] >= started+3 and calls[1][1] >= calls[0][1]+2
    store.update(job, status='paused')  # crash after snapshot commit, before final job status
    execute_job(settings, store, job, client_factory=API, wait=clock.sleep, monotonic=clock)
    assert len(calls) == 2 and len(load_profile_ranks(database, 'rank-owner', 'EUW1', 420)) == 1


def test_profile_deletion_cascades_only_its_observations_and_migration_is_idempotent(personal_rank):
    database, _, _ = personal_rank
    database.upsert_player(RiotAccount(puuid='keep', gameName='Keep', tagLine='TEST'))
    with database.connection() as c:
        for owner in ('rank-owner', 'keep'):
            c.execute('''INSERT INTO profile_rank_observations(owner_puuid,platform_region,queue_id,observed_at,status)
                         VALUES (?, 'EUW1', 420, 1000, 'unranked')''', (owner,))
    for _ in range(3):
        database.initialize()
    with database.connection() as c:
        assert c.execute('SELECT COUNT(*) FROM profile_rank_observations').fetchone()[0] == 2
        c.execute("DELETE FROM players WHERE puuid='rank-owner'")
        assert c.execute('SELECT owner_puuid FROM profile_rank_observations').fetchone()[0] == 'keep'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
        assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


@pytest.mark.parametrize('fault', ['auth', 'invalid_cooldown', 'cancel_before', 'cancel_wait', 'cancel_inflight'])
def test_failed_or_cancelled_rank_job_does_not_invent_a_point(personal_rank, fault):
    database, settings, store = personal_rank
    from tests.test_import_jobs import Clock
    from core.import_lock import ImportLock
    clock = Clock()
    store.clock = clock
    job = job_for(personal_rank)
    calls = []
    class API:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get_league_entries(self, owner):
            calls.append(owner)
            if fault == 'auth':
                raise RiotAuthenticationError('synthetic secret that must be redacted')
            if fault == 'cancel_inflight':
                store.cancel(job)
            return [entry()]
    if fault == 'invalid_cooldown':
        store.postpone(10)
        with database.connection() as c:
            c.execute('UPDATE import_cooldown SET not_before=?', (float('inf'),))
    if fault == 'cancel_before':
        store.cancel(job)
    if fault == 'cancel_wait':
        store.postpone(20)
    def wait(seconds):
        store.cancel(job)
        clock.sleep(seconds)
    with ImportLock(database.path):
        execute_job(settings, store, job, client_factory=API, wait=wait, monotonic=clock)
    assert store.get(job)['status'] == ('cancelled' if fault.startswith('cancel') else 'paused')
    assert len(calls) == (1 if fault in ('auth', 'cancel_inflight') else 0)
    assert load_profile_ranks(database, 'rank-owner', 'EUW1', 420) == []
    assert 'synthetic secret' not in str(store.get(job))
    if fault == 'cancel_wait':
        assert store.cooldown() > clock()


def test_rank_capture_uses_same_library_lock_as_other_profiles(personal_rank, monkeypatch):
    from core.import_jobs import start_import_job
    from core.import_lock import ImportLock, ImportBusyError
    database, settings, store = personal_rank
    monkeypatch.setattr('core.import_jobs._launch', lambda *a: pytest.fail('worker started despite lock'))
    with ImportLock(database.path), pytest.raises(ImportBusyError):
        start_import_job(settings, 'profile_rank', 420, owner='rank-owner')
    assert store.pending() == []


def test_equal_clock_observations_are_not_overwritten_or_given_fake_dates(personal_rank):
    database, _, store = personal_rank
    class API:
        def get_league_entries(self, owner): return [entry()]
    first = job_for(personal_rank)
    capture_profile_rank(database, API(), 'rank-owner', 'EUW1', 420, first, clock=store.clock)
    store.update(first, status='completed')
    second = job_for(personal_rank)
    with pytest.raises(sqlite3.IntegrityError):
        capture_profile_rank(database, API(), 'rank-owner', 'EUW1', 420, second, clock=store.clock)
    points = load_profile_ranks(database, 'rank-owner', 'EUW1', 420)
    assert len(points) == 1 and points[0]['job_id'] == first
