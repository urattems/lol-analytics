"""Verified SQLite online backups, including committed WAL, before deletion."""
from __future__ import annotations

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.diagnostics import BackupError


@dataclass(frozen=True)
class BackupResult:
    path: Path
    created_at: str


def backup_before_deletion(path: Path) -> BackupResult:
    """Caller holds ImportLock + BEGIN IMMEDIATE, but has NOT written yet.

    A separate read-only source sees the committed pre-delete state while the
    caller prevents concurrent writers. Backing up the caller's write transaction
    itself would block. Never use a filesystem copy of a live SQLite database.
    """
    try:
        source_path = path.resolve(strict=True)
        directory = source_path.parent / 'backups'
        directory.mkdir(exist_ok=True)
        if directory.is_symlink() or directory.resolve() != directory:
            raise BackupError()
        now = datetime.now(timezone.utc)
        target = directory / f'pre-delete-{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex}.sqlite3'
        # Reserve a new name; no overwrite of any existing snapshot or symlink.
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        deadline = time.monotonic() + 30

        def progress(_status, _remaining, _total):
            if time.monotonic() > deadline:
                raise BackupError()

        source = sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True)
        try:
            source.execute('PRAGMA query_only=ON')
            destination = sqlite3.connect(target.as_uri() + '?mode=rw', uri=True)
            try:
                source.backup(destination, pages=256, progress=progress, sleep=0.05)
                if destination.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise BackupError()
                if destination.execute('PRAGMA foreign_key_check').fetchone() is not None:
                    raise BackupError()
            finally:
                destination.close()
        finally:
            source.close()
        return BackupResult(target, now.isoformat(timespec='seconds'))
    except (OSError, sqlite3.Error, BackupError):
        # An incomplete artifact is retained for inspection, never advertised as valid.
        raise BackupError('Sauvegarde non validée ; suppression annulée.') from None
