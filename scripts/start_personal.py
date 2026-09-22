"""Start a local-only personal app, using this folder's .env explicitly."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys

from core.onboarding import read_configuration, SetupError


CONFIG_KEYS = (
    'RIOT_API_KEY', 'RIOT_GAME_NAME', 'RIOT_TAG_LINE', 'RIOT_PLATFORM_REGION',
    'RIOT_ROUTING_REGION', 'LOL_ANALYTICS_DB_PATH', 'LOL_ANALYTICS_DEMO',
)


def personal_environment(root: Path) -> dict[str, str]:
    # A shell left in demo mode or another project must not silently override
    # the profile just confirmed in this folder's assistant.
    environment = os.environ.copy()
    for key in CONFIG_KEYS:
        environment.pop(key, None)
    environment.update({key: value for key, value in read_configuration(root).values.items()
                        if key in CONFIG_KEYS and value is not None})
    environment['LOL_ANALYTICS_DEMO'] = '0'
    return environment


def available_port(first: int = 8501) -> int:
    for port in range(first, first + 20):
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                continue
            return port
    raise SetupError('Aucun port local libre. Fermez les anciennes fenêtres LoL Analytics puis réessayez.')


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    try:
        port = available_port()
        environment = personal_environment(root)
        print(f'Ouvrez http://localhost:{port} si le navigateur ne s’ouvre pas automatiquement.', flush=True)
        return subprocess.call([
            sys.executable, '-m', 'streamlit', 'run', str(root / 'app.py'),
            '--server.address', '127.0.0.1', '--server.port', str(port),
            '--server.headless', 'false', '--browser.gatherUsageStats', 'false',
        ], cwd=root, env=environment)
    except SetupError as error:
        print(str(error))
        return 1
    except OSError:
        print('Lancement impossible. Fermez les anciennes fenêtres puis relancez INSTALLER.bat.')
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
