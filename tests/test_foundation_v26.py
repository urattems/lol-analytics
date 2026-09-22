"""Previous schema, WAL snapshots, global observers and safe failures."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from config.settings import Settings
from core.db import Database
from core.diagnostics import BackupError, diagnostic, error_code, export_diagnostic, record_failure, safe_message
from core.exceptions import ProxyConfigurationError, RiotAuthenticationError, RiotAPIError, RiotNetworkError, RiotRateLimitError
from core.import_jobs import JobStore, execute_job
from core.import_lock import ImportLock
from core.models import RiotAccount, parse_riot_match
from core.network import environment_client
from core.static_data import DataDragonService
from ui.import_jobs import observe_job, watch_job
from ui.profiles import clear_profile_state


def graph(db, payload):
    db.upsert_player(RiotAccount(puuid='main', gameName='Main', tagLine='TEST'))
    db.upsert_player(RiotAccount(puuid='player-puuid', gameName='Secondary', tagLine='TEST'))
    db.insert_match(parse_riot_match(payload))
    shared = deepcopy(payload)
    shared['metadata']['matchId'] = 'EUW1_SHARED'
    shared['info']['participants'][1]['puuid'] = 'main'
    db.insert_match(parse_riot_match(shared))
    db.update_sync_state('player-puuid', payload['metadata']['matchId'])
    with db.connection() as c:
        c.execute("INSERT INTO timeline_status(match_id,status) VALUES ('EUW1_123456','available')")
        c.execute("INSERT INTO timeline_frames(match_id,participant_id,puuid,timestamp_ms,total_gold) VALUES ('EUW1_123456',1,'player-puuid',0,500)")
        c.execute("INSERT INTO timeline_events(match_id,event_index,timestamp_ms,event_type,participant_id,item_id) VALUES ('EUW1_123456',0,12000,'ITEM_PURCHASED',1,1001)")
        c.execute("INSERT INTO rank_snapshots(match_id,puuid,queue_id,status,fetched_at) VALUES ('EUW1_123456','player-puuid',420,'unranked',1000)")


def snapshot(db):
    with db.connection() as c:
        return {row[0]: [tuple(r) for r in c.execute('SELECT * FROM "' + row[0] + '" ORDER BY rowid')]
                for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def test_actual_v250_schema_migrates_three_times_without_changing_old_columns(tmp_path, riot_match_payload):
    db = Database(tmp_path / 'old.db')
    with db.connection() as c:
        c.executescript((Path(__file__).parent / 'fixtures/schema-v2.5.0.sql').read_text(encoding='utf-8'))
    graph(db, riot_match_payload)
    settings = Settings(database_path=db.path, riot_api_key='synthetic-only', riot_game_name='Secondary', riot_tag_line='TEST')
    job = JobStore(db).create(settings, 'backfill', 500)
    before = snapshot(db)
    assert len(before['import_jobs'][0]) == 18
    for _ in range(3):
        db.initialize()
        after = snapshot(db)
        for table, rows in before.items():
            assert [r[:len(rows[0])] for r in after[table]] == rows if rows else after[table] == []
        with db.connection() as c:
            assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert not c.execute('PRAGMA foreign_key_check').fetchall()
        assert JobStore(db).get(job)['diagnostic_json'] is None
        assert after['profile_rank_observations'] == []  # no retrospective copying from rank_snapshots
        assert all(after[table] == [] for table in ('match_notes', 'match_tags', 'personal_goals'))


@pytest.mark.parametrize('wal', [False, True])
def test_backup_contains_exact_pre_delete_graph_including_retained_wal(database, riot_match_payload, wal):
    reader = None
    if wal:
        reader = sqlite3.connect(database.path)
        reader.execute('PRAGMA journal_mode=WAL')
        reader.execute('BEGIN')
        reader.execute('SELECT * FROM players').fetchall()  # pinned old snapshot
    try:
        graph(database, riot_match_payload)
        before = snapshot(database)
        result = database.remove_library_profile('player-puuid', 'Main', 'TEST')
        assert result.deleted_matches == 1 and result.shared_matches == 1
        assert result.backup_created_at.endswith('+00:00')
        assert result.backup_path.parent == database.path.parent / 'backups'
        assert snapshot(Database(result.backup_path)) == before
        assert database.count_matches() == 1 and database.get_player('main')
        assert database.match_exists('EUW1_SHARED')
        with database.connection() as c:
            assert not c.execute('PRAGMA foreign_key_check').fetchall()
    finally:
        if reader:
            reader.close()


@pytest.mark.parametrize('reason', ['permission', 'disk', 'integrity'])
def test_failed_backup_never_starts_deletion(database, riot_match_payload, monkeypatch, reason):
    graph(database, riot_match_payload)
    before = snapshot(database)
    if reason == 'integrity':
        # Real malformed FK in old data: SQLite integrity_check alone is insufficient.
        with closing(sqlite3.connect(database.path)) as c:
            c.execute("INSERT INTO sync_state(puuid) VALUES ('nonexistent')")
            c.commit()
        before = snapshot(database)
    else:
        def fail(*args, **kwargs):
            raise PermissionError('synthetic private path') if reason == 'permission' else OSError('disk full')
        monkeypatch.setattr('core.backups.os.open', fail)
    with pytest.raises(BackupError):
        database.remove_library_profile('player-puuid', 'Main', 'TEST')
    assert snapshot(database) == before


def test_rejected_target_never_creates_backup(database, riot_match_payload):
    graph(database, riot_match_payload)
    with pytest.raises(ValueError):
        database.remove_library_profile('main', 'Main', 'TEST')
    assert not database.remove_library_profile('absent', 'Main', 'TEST')
    assert not (database.path.parent / 'backups').exists()


@pytest.mark.parametrize('error,code', [
    (RiotAuthenticationError('secret'), 'AUTH'), (RiotNetworkError('secret'), 'NETWORK'),
    (RiotAPIError('secret'), 'PAYLOAD_INVALID'), (RiotRateLimitError('secret'), 'RATE_LIMIT'),
    (ProxyConfigurationError('secret'), 'PROXY'), (RuntimeError('secret'), 'INTERNAL'),
])
def test_diagnostics_are_allowlisted_not_exception_redaction(error, code, caplog):
    raw = record_failure(error, phase='private/path', kind='Owner#TAG')
    assert error_code(error) == code and code in safe_message(error)
    data = json.loads(export_diagnostic(raw))
    assert data['phase'] == 'unknown' and data['kind'] is None
    assert data['sqlite_integrity'] == 'not_checked'
    assert not any(value in raw + caplog.text + safe_message(error) for value in ('secret', 'private/path', 'Owner#TAG', 'Traceback'))
    tampered = json.loads(raw)
    tampered['api_key'] = 'secret'
    assert 'secret' not in export_diagnostic(json.dumps(tampered))
    tampered['os'] = 'secret'
    assert 'secret' not in export_diagnostic(json.dumps(tampered))


def test_actual_sqlite_busy_is_categorized(database):
    with database.connection() as writer:
        writer.execute('BEGIN IMMEDIATE')
        with closing(sqlite3.connect(database.path, timeout=0)) as other:
            with pytest.raises(sqlite3.OperationalError) as failure:
                other.execute('BEGIN IMMEDIATE')
        assert error_code(failure.value) == 'DB_LOCKED'


def test_worker_initial_read_failure_is_logged_without_raw_exception(database, monkeypatch, caplog):
    store = JobStore(database)
    def fail(*args):
        raise sqlite3.DatabaseError('private path and synthetic-secret')
    monkeypatch.setattr(store, 'get', fail)
    execute_job(Settings(database_path=database.path), store, 'synthetic-job')
    assert 'DB_ERROR' in caplog.text and 'synthetic-secret' not in caplog.text


def job_for(db, name):
    settings = Settings(database_path=db.path, riot_api_key='synthetic-secret', riot_game_name=name, riot_tag_line='TEST')
    store = JobStore(db)
    return settings, store.create(settings, 'backfill', 500)


def test_global_job_survives_profile_and_session_changes_without_mixing_counts(database):
    store = JobStore(database)
    _, first = job_for(database, 'First')
    store.update(first, status='paused', local_count=12)
    settings, second = job_for(database, 'Second')
    store.update(second, status='rate_limited', local_count=85)
    for state in ({}, {'history_sentinel': 'First'}):
        assert observe_job(store, state)['job_id'] == second
        state['job_monitor_pending'] = second
        clear_profile_state(state, 'another-profile')
        assert observe_job(store, state)['local_count'] == 85
        store.update(second, status='completed')
        assert observe_job(store, state)['job_id'] == second
        state.pop('job_monitor_pending')
        assert observe_job(store, state)['job_id'] == first
        store.update(second, status='rate_limited')
    other = Database(database.path.parent / 'other.db')
    other.initialize()
    assert observe_job(JobStore(other), state) is None
    assert 'job_monitor_pending' not in state


def test_global_job_ui_tracks_foreign_profile_and_exposes_no_raw_diagnostic(database):
    settings, job_id = job_for(database, 'Import Owner')
    store = JobStore(database)
    store.update(job_id, status='rate_limited', local_count=85)
    store.postpone(42)
    app = AppTest.from_string('''
import streamlit as st
from ui.import_jobs import render_import_status
render_import_status(st.session_state['settings'], 'different-puuid')
''')
    app.session_state['settings'] = settings.model_copy(update={'riot_game_name': 'Viewing Another'})
    with ImportLock(database.path):
        app.run(timeout=15)
        assert not app.exception
        assert any('Import Owner' in m.value for m in app.markdown)
        assert '85 / 500' in app.get('progress')[0].proto.text
        assert any('Limite Riot' in i.value for i in app.info)
    store.update(job_id, status='paused', message='[AUTH]', diagnostic_json=json.dumps(diagnostic(RiotAuthenticationError('private'))))
    app.run(timeout=15)
    assert not app.exception and app.button(key='library_job_resume_' + job_id)
    assert 'private' not in app.code[0].value


def test_completed_before_first_poll_still_notifies_after_profile_switch(database):
    _, job_id = job_for(database, 'Fast')
    state = {}
    watch_job(database.path, job_id, state)
    JobStore(database).update(job_id, status='completed', local_count=1)
    clear_profile_state(state, 'another')
    assert observe_job(JobStore(database), state)['job_id'] == job_id
    assert state['job_monitor_pending'] == job_id


def test_global_ui_can_select_older_paused_job_without_network(database, monkeypatch):
    settings, first = job_for(database, 'First')
    store = JobStore(database)
    store.update(first, status='paused')
    _, second = job_for(database, 'Second')
    store.update(second, status='paused')
    calls = []
    monkeypatch.setattr('ui.import_jobs.resume_import_job', lambda s, identifier: calls.append(identifier))
    app = AppTest.from_string('''
import streamlit as st
from ui.import_jobs import render_import_status
render_import_status(st.session_state['settings'], None)
''')
    app.session_state['settings'] = settings
    app.run(timeout=15)
    app.selectbox(key='job_monitor_selected').set_value(first).run(timeout=15)
    assert not app.exception and not calls
    app.button(key='library_job_resume_' + first).click().run(timeout=15)
    assert not app.exception and calls == [first]


def test_job_persists_safe_error_and_releases_lock(database, caplog):
    settings, job_id = job_for(database, 'Private Owner')
    store = JobStore(database)
    def failed(*args, **kwargs):
        raise RiotAuthenticationError('synthetic-secret Private Owner https://private/path')
    with ImportLock(database.path):
        execute_job(settings, store, job_id, client_factory=failed)
    job = store.get(job_id)
    assert job['status'] == 'paused' and job['error_code'] == 'AUTH'
    text = job['diagnostic_json'] + job['message'] + caplog.text
    assert not any(v in text for v in ('synthetic-secret', 'Private Owner', 'https://private/path', 'Traceback'))


@pytest.mark.parametrize('value', [float('inf'), -1, 'NaN', 'not-a-timestamp'])
def test_invalid_persisted_cooldown_neither_bypasses_riot_nor_waits_forever(database, value):
    from core.import_jobs import JobAPI
    settings, job_id = job_for(database, 'Cooldown')
    store = JobStore(database)
    with database.connection() as c:
        c.execute('INSERT INTO import_cooldown VALUES (1, ?)', (value,))
    calls = []
    class Client:
        def get_match(self, *args):
            calls.append('request')
    api = JobAPI(Client(), store, store.get(job_id), wait=lambda *args: calls.append('sleep'))
    with pytest.raises(ValueError):
        api.get_match('EUW1_SYNTHETIC')
    assert calls == []


@pytest.mark.parametrize('scheme', ['http', 'https'])
def test_proxy_policy_honors_environment_and_no_proxy(monkeypatch, scheme):
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'SSL_CERT_FILE', 'SSL_CERT_DIR'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('HTTPS_PROXY', f'{scheme}://user:synthetic-secret@127.0.0.1:9999')
    monkeypatch.setenv('NO_PROXY', 'localhost,example.org')
    with environment_client() as client:
        # Transport selection only: no real network/proxy contacted.
        assert client._transport_for_url(httpx.URL('https://example.org')) is client._transport
        assert client._transport_for_url(httpx.URL('https://europe.api.riotgames.com')) is not client._transport


def test_missing_socks_support_fails_safely_and_static_data_degrades(monkeypatch, caplog):
    def unsupported(**kwargs):
        assert kwargs['trust_env'] is True
        raise ImportError('socks5://private:synthetic-secret@host')
    monkeypatch.setattr('core.network.httpx.Client', unsupported)
    with pytest.raises(ProxyConfigurationError) as failure:
        environment_client()
    assert 'synthetic-secret' not in str(failure.value)
    service = DataDragonService()
    assert service.item(1001).display_name == 'Item 1001'
    assert service.champion('Shyvana').image_url is None
    service.close()
    assert 'PROXY' in caplog.text and 'synthetic-secret' not in caplog.text
