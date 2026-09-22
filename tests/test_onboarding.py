from __future__ import annotations

from functools import partial
from io import StringIO
from pathlib import Path
import os
import socket
import subprocess
import sys
import time

from dotenv import dotenv_values
import httpx
import pytest

from config import settings as settings_module
from core.models import RiotAccount
from core.onboarding import (
    ConfigSnapshot, SetupError, VerifiedAccount, needs_setup, read_configuration,
    render_configuration, save_configuration, settings_for_import, validate_input,
    verify_account,
)
from core.riot_api import RiotAPIClient
from scripts.start_personal import available_port, personal_environment


KEY = 'RGAPI-synthetic-not-a-real-credential'


@pytest.fixture
def verified():
    inputs = validate_input('Synthetic Player#TEST', 'EUW1', KEY)
    return VerifiedAccount(inputs, RiotAccount(puuid='synthetic-puuid', gameName='Synthetic Player', tagLine='TEST'), True)


@pytest.mark.parametrize('name,platform,key', [
    ('MissingTag', 'EUW1', KEY), ('A#B#C', 'EUW1', KEY), ('A\nB#C', 'EUW1', KEY),
    ('A#B', 'attacker.invalid', KEY), ('A#B', 'EUW1', ''),
    ('A#B', 'EUW1', KEY + '\nX=bad'), ('A#B', 'EUW1', 'my-password'),
])
def test_invalid_form_rejected_without_secret_in_message(name, platform, key):
    with pytest.raises(ValueError) as error:
        validate_input(name, platform, key)
    assert KEY not in str(error.value)


def test_input_unicode_and_tag_not_inferred_as_platform():
    value = validate_input('  Élève 桜#NA1 ', 'euw1', ' ' + KEY + ' ')
    assert value.riot_id == 'Élève 桜#NA1'
    assert value.platform == 'EUW1' and value.routing == 'EUROPE'
    assert KEY not in repr(value)


def make_client(handler):
    return partial(RiotAPIClient, transport=httpx.MockTransport(handler))


@pytest.mark.parametrize('platform,account_host,match_host', [
    ('EUW1', 'europe', 'europe'), ('NA1', 'americas', 'americas'),
    ('KR', 'asia', 'asia'), ('OC1', 'asia', 'sea'),
])
@pytest.mark.parametrize('has_history', [True, False])
def test_verification_checks_actual_platform_and_all_access(platform, account_host, match_host, has_history):
    requests = []
    def handler(request):
        requests.append(request)
        assert request.headers['X-Riot-Token'] == KEY
        assert KEY not in str(request.url)
        if '/account/' in request.url.path:
            return httpx.Response(200, json={'puuid': 'synthetic/p', 'gameName': 'QA', 'tagLine': 'TAG'})
        if '/summoner/' in request.url.path:
            return httpx.Response(200, json={'puuid': 'synthetic/p'})
        return httpx.Response(200, json=['SYNTHETIC_1'] if has_history else [])
    progress = []
    result = verify_account(validate_input('QA#TAG', platform, KEY),
                            client_factory=make_client(handler), progress=progress.append)
    assert result.has_history is has_history
    assert len(progress) == 3
    assert [r.url.host for r in requests] == [f'{account_host}.api.riotgames.com', f'{platform.lower()}.api.riotgames.com', f'{match_host}.api.riotgames.com']
    assert 'synthetic%2Fp' in str(requests[1].url)
    assert requests[2].url.params['count'] == '1'


@pytest.mark.parametrize('status,expected', [(401, '401/403'), (403, '401/403'), (404, 'Riot ID introuvable'), (429, '429'), (500, 'indisponible')])
def test_api_failures_actionable_no_retry_or_secret(status, expected, tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={'Retry-After': '120'}, json={'secret': KEY})
    with pytest.raises(SetupError, match=expected) as error:
        verify_account(validate_input('QA#TAG', 'EUW1', KEY), client_factory=make_client(handler))
    assert len(calls) == 1
    assert KEY not in str(error.value)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('stage', ['platform', 'history'])
def test_wrong_server_or_history_access_has_specific_message(stage):
    def handler(request):
        if '/account/' in request.url.path:
            return httpx.Response(200, json={'puuid': 'p', 'gameName': 'QA', 'tagLine': 'TAG'})
        if '/summoner/' in request.url.path and stage == 'history':
            return httpx.Response(200, json={'puuid': 'p'})
        return httpx.Response(404)
    expected = 'aucun profil LoL' if stage == 'platform' else 'historique'
    with pytest.raises(SetupError, match=expected):
        verify_account(validate_input('QA#TAG', 'EUW1', KEY), client_factory=make_client(handler))


@pytest.mark.parametrize('payload', [None, {}, {'puuid': 'other'}, [], {'puuid': 12}])
def test_platform_response_cannot_confirm_another_identity(payload):
    def handler(request):
        if '/account/' in request.url.path:
            return httpx.Response(200, json={'puuid': 'p', 'gameName': 'QA', 'tagLine': 'TAG'})
        return httpx.Response(200, json=payload)
    with pytest.raises(SetupError, match='inexploitable'):
        verify_account(validate_input('QA#TAG', 'EUW1', KEY), client_factory=make_client(handler))


def test_network_error_is_safe():
    def handler(request):
        raise httpx.ConnectError(KEY, request=request)
    with pytest.raises(SetupError, match='Connexion') as error:
        verify_account(validate_input('QA#TAG', 'EUW1', KEY), client_factory=make_client(handler))
    assert KEY not in str(error.value)


def test_fresh_save_and_no_db_write(tmp_path, verified):
    snapshot = read_configuration(tmp_path)
    assert needs_setup(tmp_path)
    assert save_configuration(tmp_path, snapshot, verified) is None
    saved = read_configuration(tmp_path)
    assert saved.values['RIOT_GAME_NAME'] == 'Synthetic Player'
    assert saved.values['RIOT_API_KEY'] == KEY
    assert saved.values['LOL_ANALYTICS_DEMO'] == '0'
    assert not needs_setup(tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ['.env']
    assert KEY not in repr(saved)
    assert settings_for_import(tmp_path, verified).database_path == tmp_path / 'data/lol_analytics.db'


def test_update_preserves_existing_database_comments_and_multiline(tmp_path, verified):
    original = ("# keep this comment\r\nLOL_ANALYTICS_DB_PATH='custom/my history.db'\r\n"
                "CUSTOM='line1\nRIOT_API_KEY=inside-other-setting\nline3'\r\n"
                "export RIOT_API_KEY='old'\r\nRIOT_API_KEY='duplicate'\r\nRIOT_GAME_NAME=Old\r\n")
    path = tmp_path / '.env'
    path.write_bytes(original.encode('utf-8'))
    snapshot = read_configuration(tmp_path)
    backup = save_configuration(tmp_path, snapshot, verified)
    assert backup.read_bytes() == original.encode('utf-8')
    current = read_configuration(tmp_path)
    assert current.values['CUSTOM'] == 'line1\nRIOT_API_KEY=inside-other-setting\nline3'
    assert current.values['LOL_ANALYTICS_DB_PATH'] == 'custom/my history.db'
    assert current.values['RIOT_API_KEY'] == KEY
    assert path.read_text(encoding='utf-8').startswith('# keep this comment')
    assert not (tmp_path / '.env.setup.lock').exists()


def test_changed_configuration_is_not_overwritten(tmp_path, verified):
    snapshot = read_configuration(tmp_path)
    (tmp_path / '.env').write_text('OTHER=concurrent-change', encoding='utf-8')
    with pytest.raises(SetupError, match='changé'):
        save_configuration(tmp_path, snapshot, verified)
    assert (tmp_path / '.env').read_text() == 'OTHER=concurrent-change'


def test_atomic_replace_failure_keeps_original_and_backup(tmp_path, monkeypatch, verified):
    path = tmp_path / '.env'
    original = b'RIOT_GAME_NAME=Original\nRIOT_TAG_LINE=TAG\n'
    path.write_bytes(original)
    def fail(*args):
        raise PermissionError('locked')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(SetupError, match='Enregistrement impossible'):
        save_configuration(tmp_path, read_configuration(tmp_path), verified)
    assert path.read_bytes() == original
    assert next(tmp_path.glob('.env.backup-*')).read_bytes() == original
    assert not list(tmp_path.glob('.env.setup*'))


def test_exclusive_lock_is_respected(tmp_path, verified):
    lock = tmp_path / '.env.setup.lock'
    lock.touch()
    with pytest.raises(SetupError, match='autre assistant'):
        save_configuration(tmp_path, read_configuration(tmp_path), verified)
    assert lock.exists() and not (tmp_path / '.env').exists()


def test_invalid_existing_configuration_is_not_rewritten(tmp_path):
    content = b"SECRET='unterminated"
    (tmp_path / '.env').write_bytes(content)
    with pytest.raises(SetupError, match='ligne invalide') as error:
        read_configuration(tmp_path)
    assert 'unterminated' not in str(error.value)
    assert (tmp_path / '.env').read_bytes() == content


def test_literals_roundtrip_in_actual_settings_loader(tmp_path, monkeypatch, verified):
    account = RiotAccount(puuid='p', gameName="Élève ' ${SYNTHETIC_SECRET} \\ 桜", tagLine='TAG')
    verified = VerifiedAccount(verified.settings, account, False)
    monkeypatch.setenv('SYNTHETIC_SECRET', 'must-not-expand')
    save_configuration(tmp_path, ConfigSnapshot(None, {}), verified)
    monkeypatch.setattr(settings_module, 'PROJECT_ROOT', tmp_path)
    for key in read_configuration(tmp_path).values:
        monkeypatch.delenv(key, raising=False)
    settings_module.get_settings.cache_clear()
    try:
        assert settings_module.get_settings().riot_game_name == account.game_name
    finally:
        settings_module.get_settings.cache_clear()
        for key in read_configuration(tmp_path).values:
            os.environ.pop(key, None)


def test_launcher_uses_saved_profile_not_inherited_demo(tmp_path, monkeypatch, verified):
    save_configuration(tmp_path, read_configuration(tmp_path), verified)
    monkeypatch.setenv('RIOT_GAME_NAME', 'Wrong inherited profile')
    monkeypatch.setenv('LOL_ANALYTICS_DB_PATH', 'wrong.db')
    monkeypatch.setenv('LOL_ANALYTICS_DEMO', '1')
    environment = personal_environment(tmp_path)
    assert environment['RIOT_GAME_NAME'] == 'Synthetic Player'
    assert environment['LOL_ANALYTICS_DB_PATH'] == 'data/lol_analytics.db'
    assert environment['LOL_ANALYTICS_DEMO'] == '0'
    assert os.environ['LOL_ANALYTICS_DEMO'] == '1'


def test_busy_port_does_not_open_another_application():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        if port > 65515:
            pytest.skip('Ephemeral port too close to range end')
        assert available_port(port) > port


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows desktop integration')
def test_shortcut_real_com_creation_idempotence_and_no_overwrite(tmp_path):
    root = Path(__file__).resolve().parent.parent
    desktop = tmp_path / "Bureau test & accents é ' %"
    desktop.mkdir()
    existing = desktop / 'LoL Analytics.lnk'
    # A preexisting link to a different target must remain byte-identical.
    existing.write_bytes(b'not our shortcut')
    command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(root / 'scripts/create_shortcut.ps1'), '-DesktopPath', str(desktop)]
    for _ in range(2):
        completed = subprocess.run(command, capture_output=True)
        assert completed.returncode == 0, completed.stderr
    assert existing.read_bytes() == b'not our shortcut'
    created = desktop / 'LoL Analytics (2).lnk'
    assert created.stat().st_size > 100
    assert len(list(desktop.iterdir())) == 2


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Tk GUI needs a desktop')
def test_wizard_real_widgets_verify_confirm_save_and_close(tmp_path, monkeypatch, verified):
    result = subprocess.run([sys.executable, '-m', 'tests.gui_first_run_probe', 'verify', str(tmp_path)], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Tk GUI needs a desktop')
def test_wizard_cancel_and_demo_never_write_configuration(tmp_path):
    for action, expected in [('close', 2), ('demo', 3)]:
        result = subprocess.run([sys.executable, '-m', 'tests.gui_first_run_probe', action, str(tmp_path)], capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(sys.platform != 'win32', reason='Tk entrypoint imports')
def test_optional_import_failure_keeps_configuration_and_is_actionable(tmp_path, monkeypatch, verified):
    from scripts import first_run
    from core.exceptions import RiotRateLimitError
    monkeypatch.setattr(first_run, 'import_profile', lambda *_args, **_kwargs: (_ for _ in ()).throw(RiotRateLimitError(KEY)))
    monkeypatch.setattr(first_run, 'create_shortcut', lambda root: False)
    messages = first_run.complete_setup(tmp_path, read_configuration(tmp_path), verified,
                                       import_games=True, shortcut=True, progress=lambda _: None)
    assert read_configuration(tmp_path).values['RIOT_API_KEY'] == KEY
    assert any('Synchroniser' in message for message in messages)
    assert any('raccourci n’a pas pu' in message for message in messages)
    assert KEY not in ' '.join(messages)
