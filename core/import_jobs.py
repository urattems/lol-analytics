"""Persistent, cancellable import jobs, independent of Streamlit's render thread.

Credentials live only in the running worker's Settings. SQLite stores the
requested scope, verified identity, discovered ID pages and completed progress.
Existing SyncService remains the authority for match validation and anchors.
"""
from __future__ import annotations

from collections.abc import Callable
import json
import math
import sqlite3
import threading
import time
import uuid

from config.settings import Settings
from core.db import Database
from core.diagnostics import record_failure, safe_message, error_code
from core.exceptions import RiotConfigurationError, RiotRateLimitError
from core.import_lock import ImportLock
from core.profiles import parse_riot_id, routing_for_platform
from core.riot_api import RiotAPIClient
from core.sync import SyncService


TERMINAL = {'completed', 'cancelled'}
_workers: dict[str, threading.Thread] = {}
_registry_lock = threading.Lock()


class ImportCancelled(Exception):
    pass


class JobStore:
    def __init__(self, database: Database, clock: Callable[[], float] = time.time):
        self.database, self.clock = database, clock

    def get(self, job_id: str) -> dict | None:
        with self.database.connection() as connection:
            row = connection.execute('SELECT * FROM import_jobs WHERE job_id=?', (job_id,)).fetchone()
        return dict(row) if row else None

    def latest(self, game_name: str, tag_line: str, puuid: str | None = None) -> dict | None:
        with self.database.connection() as connection:
            row = connection.execute('''SELECT * FROM import_jobs
                WHERE (puuid=? AND ? IS NOT NULL) OR (game_name=? AND tag_line=?)
                ORDER BY created_at DESC, rowid DESC LIMIT 1''', (puuid, puuid, game_name, tag_line)).fetchone()
        return dict(row) if row else None

    def foreground(self) -> dict | None:
        """Global library job: active first, then resumable, then last result.

        No active-profile predicate: switching profiles must not hide a worker.
        A crashed running job remains resumable once its OS lock is released.
        """
        with self.database.connection() as connection:
            row = connection.execute('''SELECT * FROM import_jobs ORDER BY
                CASE WHEN status IN ('queued','running','rate_limited','cancel_requested') THEN 0
                     WHEN status='paused' THEN 1 ELSE 2 END,
                updated_at DESC, rowid DESC LIMIT 1''').fetchone()
        return dict(row) if row else None

    def pending(self) -> list[dict]:
        with self.database.connection() as connection:
            return [dict(row) for row in connection.execute('''SELECT * FROM import_jobs
                WHERE status NOT IN ('completed','cancelled') ORDER BY updated_at DESC, rowid DESC''')]

    def create(self, settings: Settings, kind: str, target: int | None, *, match_id: str | None = None, owner: str | None = None) -> str:
        if settings.demo_mode or not settings.riot_api_key:
            raise RiotConfigurationError('Une clé Riot est nécessaire hors mode démo.')
        parse_riot_id(settings.riot_id)
        routing = routing_for_platform(settings.riot_platform_region)
        if kind not in {'backfill', 'sync', 'recent', 'ranks', 'profile_rank'}:
            raise ValueError('Type d’import invalide.')
        if target is not None and (isinstance(target, bool) or not isinstance(target, int) or not 1 <= target <= 100000):
            raise ValueError('Choisissez un objectif entre 1 et 100 000 parties.')
        if kind == 'recent' and (target is None or target > 100):
            raise ValueError('L’import récent accepte 1 à 100 parties.')
        player = self.database.get_player(owner) if owner else self.database.find_player(settings.riot_game_name, settings.riot_tag_line)
        if owner and (not player or (str(player['game_name']).casefold(), str(player['tag_line']).casefold()) !=
                      (settings.riot_game_name.casefold(), settings.riot_tag_line.casefold())):
            raise ValueError('Le profil du job ne correspond pas au profil analysé.')
        puuid = str(player['puuid']) if player else None
        platform = settings.riot_platform_region
        if kind == 'ranks':
            from core.ranks import load_rank_contexts
            context = load_rank_contexts(self.database, puuid or '', [match_id]).get(match_id)
            if not context or not puuid:
                raise ValueError('Choisissez une partie Ranked appartenant à ce profil.')
            if context.observed >= context.total:
                raise ValueError('Cet instantané est déjà complet ; il reste conservé.')
            platform = str(match_id).partition('_')[0]
            routing = routing_for_platform(platform)
        elif match_id is not None:
            raise ValueError('Un match précis ne concerne que l’enrichissement des rangs.')
        if kind == 'profile_rank' and (not puuid or target not in (420, 440)):
            raise ValueError('Choisissez un profil enregistré et une file Ranked Solo/Duo ou Flex.')
        previous = self.latest(settings.riot_game_name, settings.riot_tag_line, puuid)
        if previous and previous['status'] not in TERMINAL:
            raise ValueError('Un import attend déjà pour ce profil. Reprenez-le ou annulez-le avant d’en lancer un autre.')
        count = self.database.count_player_matches(puuid) if puuid else 0
        job_id, now = uuid.uuid4().hex, self.clock()
        with self.database.connection() as connection:
            connection.execute('''INSERT INTO import_jobs
                (job_id, puuid, game_name, tag_line, platform_region, routing_region,
                 kind, target, match_id, status, created_at, updated_at, local_before, local_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)''',
                (job_id, puuid, settings.riot_game_name, settings.riot_tag_line,
                 platform, routing, kind, target, match_id, now, now, count, count))
        return job_id

    def update(self, job_id: str, **values):
        allowed = {'puuid', 'status', 'local_count', 'completed', 'total', 'phase', 'message', 'error_code', 'diagnostic_json'}
        if not set(values) <= allowed:
            raise ValueError('Invalid job update')
        values['updated_at'] = self.clock()
        assignments, parameters = [], []
        for key, value in values.items():
            if key == 'status':
                assignments.append("status=CASE WHEN status='cancel_requested' AND ? <> 'cancelled' THEN status ELSE ? END")
                parameters.extend((value, value))
            else:
                assignments.append(f'{key}=?')
                parameters.append(value)
        with self.database.connection() as connection:
            connection.execute('UPDATE import_jobs SET ' + ', '.join(assignments) + ' WHERE job_id=?',
                               (*parameters, job_id))

    def cancel(self, job_id: str):
        # Worker progress must never overwrite this flag. Status alone would
        # race with a callback that reports a completed match.
        with self.database.connection() as connection:
            connection.execute("UPDATE import_jobs SET status='cancel_requested', updated_at=? WHERE job_id=? AND status NOT IN ('completed', 'cancelled')",
                               (self.clock(), job_id))

    def cooldown(self) -> float:
        with self.database.connection() as connection:
            row = connection.execute('SELECT not_before FROM import_cooldown WHERE singleton=1').fetchone()
        deadline = float(row[0]) if row else 0.0
        if not math.isfinite(deadline) or deadline < 0:
            raise ValueError('Délai Riot local invalide : aucun appel envoyé.')
        return deadline

    def postpone(self, seconds: float):
        with self.database.connection() as connection:
            connection.execute('''INSERT INTO import_cooldown VALUES (1, ?)
                ON CONFLICT(singleton) DO UPDATE SET not_before=MAX(not_before, excluded.not_before)''',
                (self.clock() + seconds,))

    def page(self, job_id: str, key: str) -> list[str] | None:
        with self.database.connection() as connection:
            row = connection.execute('SELECT match_ids_json FROM import_job_pages WHERE job_id=? AND request_key=?', (job_id, key)).fetchone()
        if row is None:
            return None
        values = json.loads(row[0])
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ValueError('Invalid persisted page')
        return values

    def save_page(self, job_id: str, key: str, values: list[str]):
        with self.database.connection() as connection:
            connection.execute('INSERT OR IGNORE INTO import_job_pages VALUES (?, ?, ?)', (job_id, key, json.dumps(values)))


class JobAPI:
    """Retry the exact failed request after a cancellable shared cooldown."""
    def __init__(self, client, store: JobStore, job: dict, *, wait=time.sleep, monotonic=time.monotonic):
        self.client, self.store, self.job = client, store, job
        self.wait, self.monotonic = wait, monotonic
        self.deadline = 0.0

    def guard(self):
        current = self.store.get(self.job['job_id'])
        if current is None or current['status'] in {'cancel_requested', 'cancelled'}:
            raise ImportCancelled()

    def call(self, method: str, *args, **kwargs):
        while True:
            self.guard()
            remaining = max(self.store.cooldown() - self.store.clock(), self.deadline - self.monotonic())
            if remaining > 0:
                self.store.update(self.job['job_id'], status='rate_limited', message='Limite Riot atteinte — reprise automatique après le délai.')
                while remaining > 0:
                    self.guard()
                    self.wait(min(0.25, remaining))
                    remaining = max(self.store.cooldown() - self.store.clock(), self.deadline - self.monotonic())
                self.guard()
            self.store.update(self.job['job_id'], status='running', message=None)
            self.guard()
            try:
                result = getattr(self.client, method)(*args, **kwargs)
                self.guard()
                return result
            except RiotRateLimitError as error:
                if error.retry_after is None or not math.isfinite(error.retry_after) or error.retry_after < 0:
                    raise
                # A zero header must not create a tight request loop.
                seconds = max(1.0, error.retry_after) + 0.1
                self.store.postpone(seconds)
                self.deadline = self.monotonic() + seconds

    def resolve_account(self, game_name, tag_line):
        account = self.call('resolve_account', game_name, tag_line)
        if self.job['puuid'] and account.puuid != self.job['puuid']:
            raise ValueError('Le Riot ID ne correspond plus au profil de ce job.')
        return account

    def get_match_ids(self, puuid, start=0, count=20, queue=None):
        self.guard()
        self.store.update(self.job['job_id'], puuid=puuid)
        key = json.dumps([puuid, start, count, queue])
        saved = self.store.page(self.job['job_id'], key)
        if saved is not None:
            return saved
        result = self.call('get_match_ids', puuid, start=start, count=count, queue=queue)
        self.store.save_page(self.job['job_id'], key, result)
        return result

    def get_match(self, match_id):
        return self.call('get_match', match_id)

    def get_league_entries(self, puuid):
        return self.call('get_league_entries', puuid)


def _safe_error(error: Exception) -> str:
    return safe_message(error)


def execute_job(settings: Settings, store: JobStore, job_id: str, *,
                client_factory=RiotAPIClient, wait=time.sleep, monotonic=time.monotonic):
    """Run with ImportLock already held (testable without spawning a thread)."""
    try:
        job = store.get(job_id)
    except sqlite3.Error as error:
        record_failure(error)
        return
    if job is None or job['status'] in TERMINAL:
        return
    phase = 'preparing'
    try:
        if job['status'] == 'cancel_requested':
            raise ImportCancelled()
        if not settings.riot_api_key or settings.demo_mode:
            raise RiotConfigurationError('Missing key')
        if job['puuid'] and not store.database.get_player(job['puuid']):
            raise ValueError('Profile no longer exists')
        with client_factory(settings.riot_api_key, routing_region=job['routing_region'],
                            platform_region=job['platform_region'], defer_rate_limits=True) as client:
            api = JobAPI(client, store, job, wait=wait, monotonic=monotonic)
            if job['kind'] == 'profile_rank':
                phase = 'profile_rank'
                from core.profile_ranks import capture_profile_rank
                from core.ranks import rank_label
                api.guard()
                store.update(job_id, phase=phase, completed=0, total=1)
                observation = capture_profile_rank(store.database, api, job['puuid'], job['platform_region'],
                                                   job['target'], job_id, clock=store.clock)
                api.guard()
                store.update(job_id, status='completed', phase=phase, completed=1, total=1,
                             message=f'Rang personnel observé : {rank_label(observation)}. Date de récupération conservée ; aucun rang passé reconstitué.')
                return
            if job['kind'] == 'ranks':
                phase = 'ranks'
                from core.ranks import capture_rank_snapshot
                def rank_progress(current, total):
                    api.guard()
                    store.update(job_id, phase='ranks', completed=current, total=total)
                    api.guard()
                snapshot = capture_rank_snapshot(store.database, api, job['match_id'], job['puuid'],
                                                 progress=rank_progress, clock=store.clock)
                api.guard()
                store.update(job_id, status='completed', phase='ranks',
                             message=f'Instantané conservé : {snapshot.ranked}/{snapshot.total} rangs exploitables. Il décrit les rangs récupérés maintenant, pas ceux à la date de la partie.')
                return
            service = SyncService(api, store.database, job['platform_region'], job['routing_region'])
            def progress(event):
                nonlocal phase
                phase = event.phase
                api.guard()
                current = store.get(job_id)
                puuid = current['puuid']
                count = store.database.count_player_matches(puuid) if puuid else 0
                store.update(job_id, phase=event.phase, completed=event.current,
                             total=event.total, local_count=count)
                api.guard()
            arguments = (job['game_name'], job['tag_line'], job['target'], progress)
            if job['kind'] == 'backfill':
                result = service.backfill_history(*arguments, expected_puuid=job['puuid'])
            elif job['kind'] == 'recent':
                result = service.import_recent_player(*arguments, expected_puuid=job['puuid'])
            else:
                result = service.sync_player(*arguments, expected_puuid=job['puuid'])
            api.guard()
            count = store.database.count_player_matches(result.account.puuid)
            complete = not (job['kind'] == 'backfill' and not result.history_exhausted
                            and (job['target'] is None or count < job['target']))
            store.update(job_id, status='completed' if complete else 'paused', phase='finished',
                         puuid=result.account.puuid, local_count=count,
                         message=(f'Import terminé : {max(0, count - job["local_before"])} nouvelle(s) partie(s), {count} disponible(s) localement.'
                                  if complete else 'Pagination Riot interrompue sans atteindre l’objectif. Les parties sont conservées.'))
    except ImportCancelled:
        current = store.get(job_id)
        count = store.database.count_player_matches(current['puuid']) if current and current['puuid'] else 0
        store.update(job_id, status='cancelled', local_count=count, message='Import annulé. Les parties déjà enregistrées sont conservées.')
    except Exception as error:
        # Only controlled messages are persisted; never exception repr, body or key.
        payload = record_failure(error, phase=phase, kind=job['kind'])
        try:
            store.update(job_id, status='paused', message=_safe_error(error),
                         error_code=error_code(error), diagnostic_json=payload)
        except sqlite3.Error as storage_error:
            # The DB may itself be locked/unwritable. Keep the last checkpoint;
            # OS lock release makes that interrupted job resumable on next run.
            record_failure(storage_error, kind=job['kind'])
    finally:
        try:
            current = store.get(job_id)
            if current and current['status'] == 'cancel_requested':
                store.update(job_id, status='cancelled', message='Import annulé. Les parties déjà enregistrées sont conservées.')
        except sqlite3.Error as error:
            record_failure(error, kind=job['kind'])


def _launch(settings: Settings, store: JobStore, job_id: str, lock: ImportLock) -> str:
    def run():
        try:
            execute_job(settings, store, job_id)
        except Exception as error:
            # Last worker boundary: even an unexpected startup failure must not
            # print a credential-bearing exception from a daemon thread.
            record_failure(error)
        finally:
            lock.__exit__()
            with _registry_lock:
                _workers.pop(job_id, None)
    thread = threading.Thread(target=run, name='lol-import-' + job_id[:8], daemon=True)
    with _registry_lock:
        _workers[job_id] = thread
    try:
        thread.start()
    except BaseException:
        with _registry_lock:
            _workers.pop(job_id, None)
        lock.__exit__()
        raise
    return job_id


def start_import_job(settings: Settings, kind: str, target: int | None, *, match_id: str | None = None, owner: str | None = None) -> str:
    database = Database(settings.database_path)
    database.initialize()
    lock = ImportLock(database.path).__enter__()
    try:
        store = JobStore(database)
        job_id = store.create(settings, kind, target, match_id=match_id, owner=owner)
    except BaseException:
        lock.__exit__()
        raise
    return _launch(settings, store, job_id, lock)


def resume_import_job(settings: Settings, job_id: str) -> str:
    database = Database(settings.database_path)
    lock = ImportLock(database.path).__enter__()
    try:
        store = JobStore(database)
        job = store.get(job_id)
        if job is None or job['status'] in TERMINAL:
            raise ValueError('Ce job est terminé ou n’existe plus.')
        if not settings.riot_api_key or settings.demo_mode:
            raise RiotConfigurationError('Une clé Riot est nécessaire hors mode démo.')
        # The process may have died while running/waiting. Acquiring the OS
        # lock proves there is no old worker still performing this import.
        store.update(job_id, status='queued', message=None, error_code=None, diagnostic_json=None)
    except BaseException:
        lock.__exit__()
        raise
    return _launch(settings, store, job_id, lock)


def cancel_import_job(settings: Settings, job_id: str):
    store = JobStore(Database(settings.database_path))
    store.cancel(job_id)
    try:
        with ImportLock(settings.database_path):
            job = store.get(job_id)
            if job and job['status'] == 'cancel_requested':
                store.update(job_id, status='cancelled', message='Import annulé. Les parties déjà enregistrées sont conservées.')
    except ValueError:
        pass  # The running worker observes the flag at its next safe boundary.
