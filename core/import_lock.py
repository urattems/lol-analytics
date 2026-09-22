"""One import/cleanup owner per SQLite library, including across processes.

The OS releases the lock after a crash. The empty marker may remain on disk;
its existence is not evidence that an import is still running.
"""
from __future__ import annotations

import os
import math
import sqlite3
import time
from contextlib import closing
from pathlib import Path


class ImportBusyError(ValueError):
    pass


def ensure_riot_ready(database_path: str | Path, *, clock=time.time) -> None:
    """Bounded legacy actions must also respect a background job's cooldown.

    Call while holding ImportLock. A missing/pre-job database has no saved
    cooldown. This read never creates a database or migrates personal data.
    """
    path = Path(database_path).resolve()
    if not path.exists():
        return
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
        connection.execute('PRAGMA query_only=ON')
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='import_cooldown'").fetchone():
            return
        row = connection.execute('SELECT not_before FROM import_cooldown WHERE singleton=1').fetchone()
    if row is None:
        return
    try:
        remaining = float(row[0]) - clock()
    except (TypeError, ValueError, OverflowError):
        remaining = math.inf
    if not math.isfinite(remaining):
        raise ImportBusyError('Délai Riot local invalide : aucun appel envoyé. Vérifiez l’horloge et la base.')
    if remaining > 0:
        raise ImportBusyError(f'Limite Riot encore active : attendez {math.ceil(remaining)} s avant cette action. Le job peut attendre et reprendre automatiquement.')


class ImportLock:
    def __init__(self, database_path: str | Path):
        self.path = Path(str(Path(database_path).resolve()) + '.import.lock')
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = open(self.path, 'a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if self.path.stat().st_size == 0:
                    self.stream.write(b'0')
                    self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            self.stream = None
            raise ImportBusyError('Un import ou nettoyage est en cours. Annulez-le et attendez son arrêt avant de continuer.') from None
        return self

    def __exit__(self, *_):
        if self.stream is not None:
            self.stream.close()
            self.stream = None


def import_is_running(database_path: str | Path) -> bool:
    try:
        with ImportLock(database_path):
            return False
    except ImportBusyError:
        return True
