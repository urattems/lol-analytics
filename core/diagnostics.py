"""Allowlisted support diagnostics. Never serialize an exception or user input."""
from __future__ import annotations

import json
import logging
import platform
import sqlite3
import sys
from datetime import datetime, timezone

import httpx

from config.version import APP_VERSION
from core.import_lock import ImportBusyError
from core.exceptions import (
    RiotAPIError, RiotAuthenticationError, RiotConfigurationError, RiotNetworkError,
    RiotNotFoundError, RiotRateLimitError, RiotServerError, ProxyConfigurationError,
)

LOGGER = logging.getLogger(__name__)
MESSAGES = {
    'AUTH': 'Clé Riot refusée ou expirée. Renouvelez-la avec INSTALLER.bat puis redémarrez l’application.',
    'CONFIG': 'Configuration Riot incomplète. Vérifiez-la avec INSTALLER.bat.',
    'PROXY': 'Configuration proxy non utilisable. Vérifiez HTTP_PROXY, HTTPS_PROXY, ALL_PROXY et NO_PROXY. Un proxy SOCKS nécessite httpx[socks].',
    'NETWORK': 'Riot ou le réseau est indisponible. Les parties enregistrées sont conservées ; vous pouvez reprendre.',
    'RATE_LIMIT': 'Délai Retry-After inexploitable ou excessif : import en pause, aucune tentative anticipée.',
    'NOT_FOUND': 'Riot ne trouve pas ce compte ou cette ressource. Vérifiez le Riot ID et le serveur.',
    'DB_LOCKED': 'Base locale occupée. Attendez la fin de l’autre opération, puis réessayez.',
    'DB_ERROR': 'La base locale ne peut pas être utilisée. Ne la réinitialisez pas ; conservez-la pour diagnostic.',
    'PAYLOAD_INVALID': 'Données reçues ou paramètres invalides. L’étape est arrêtée sans inventer de valeurs.',
    'BACKUP': 'Sauvegarde non validée : suppression annulée. Vérifiez l’espace disque et les droits du dossier backups.',
    'INTERNAL': 'Cette étape a échoué. Les données déjà enregistrées sont conservées. Le diagnostic expurgé peut être partagé.',
}
PHASES = {'preparing', 'discovering', 'importing', 'finished', 'ranks', 'resume', 'cancel', 'profiles', 'sync', 'backfill', 'timeline', 'metadata', 'removal', 'startup', 'render', 'chart'}
KINDS = {'backfill', 'sync', 'recent', 'ranks', 'profile_rank'}
PHASES.add('profile_rank')
PHASES.add('journal')


class BackupError(Exception):
    """A validated pre-deletion snapshot could not be created."""


def error_code(error: Exception) -> str:
    if isinstance(error, ImportBusyError):
        return 'DB_LOCKED'
    if isinstance(error, BackupError):
        return 'BACKUP'
    if isinstance(error, ProxyConfigurationError):
        return 'PROXY'
    if isinstance(error, RiotAuthenticationError):
        return 'AUTH'
    if isinstance(error, RiotConfigurationError):
        return 'CONFIG'
    if isinstance(error, RiotRateLimitError):
        return 'RATE_LIMIT'
    if isinstance(error, RiotNotFoundError):
        return 'NOT_FOUND'
    if isinstance(error, (RiotNetworkError, RiotServerError, httpx.HTTPError)):
        return 'NETWORK'
    if isinstance(error, sqlite3.Error):
        code = getattr(error, 'sqlite_errorcode', 0) or 0
        return 'DB_LOCKED' if code & 0xff in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) else 'DB_ERROR'
    if isinstance(error, (RiotAPIError, ValueError, TypeError)):
        return 'PAYLOAD_INVALID'
    return 'INTERNAL'


def safe_message(error: Exception) -> str:
    code = error_code(error)
    return f'[{code}] {MESSAGES[code]}'


def diagnostic(error: Exception, *, phase: str = 'preparing', kind: str | None = None) -> dict:
    return {
        'schema': 1, 'app_version': APP_VERSION,
        'python': '.'.join(map(str, sys.version_info[:3])),
        'os': platform.system() if platform.system() in {'Windows', 'Linux', 'Darwin'} else 'Other',
        'error_code': error_code(error),
        'phase': phase if phase in PHASES else 'unknown',
        'kind': kind if kind in KINDS else None,
        'occurred_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'sqlite_integrity': 'not_checked',
    }


def record_failure(error: Exception, *, phase: str = 'preparing', kind: str | None = None) -> str:
    payload = json.dumps(diagnostic(error, phase=phase, kind=kind), ensure_ascii=True, sort_keys=True)
    LOGGER.warning('Local operation failed: %s', payload)  # no exc_info, traceback or repr
    return payload


def export_diagnostic(raw: str | None) -> str:
    """Revalidate the persisted boundary too: an old/tampered DB is not trusted."""
    try:
        data = json.loads(raw or '{}')
        if not isinstance(data, dict):
            raise ValueError()
        clean = {key: data[key] for key in ('schema', 'app_version', 'python', 'os', 'error_code', 'phase', 'kind', 'occurred_at', 'sqlite_integrity')}
        import re
        valid = (
            type(clean['schema']) is int and clean['schema'] == 1
            and all(isinstance(clean[k], str) and re.fullmatch(r'\d{1,3}\.\d{1,3}\.\d{1,3}', clean[k]) for k in ('app_version', 'python'))
            and clean['os'] in {'Windows', 'Linux', 'Darwin', 'Other'}
            and clean['error_code'] in MESSAGES and clean['phase'] in PHASES | {'unknown'}
            and clean['kind'] in KINDS | {None} and clean['sqlite_integrity'] == 'not_checked'
            and isinstance(clean['occurred_at'], str) and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00', clean['occurred_at'])
        )
        if not valid:
            raise ValueError()
        return json.dumps(clean, ensure_ascii=True, sort_keys=True, indent=2)
    except (ValueError, TypeError, KeyError):
        return json.dumps({'schema': 1, 'error_code': 'INTERNAL', 'diagnostic': 'unavailable'})
