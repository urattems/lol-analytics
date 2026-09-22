"""Start the app against an isolated synthetic dataset, without editing .env."""
from __future__ import annotations

import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from scripts.create_demo import DEMO_GAME_NAME, DEMO_TAG_LINE, DEMO_PUUID, create_demo_database


def prepare_demo(path: Path) -> None:
    if not path.exists():
        create_demo_database(path, journal_examples=True)
        return
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as connection:
        identities = connection.execute("SELECT puuid FROM participants").fetchall()
        players = connection.execute("SELECT puuid FROM players").fetchall()
    if not identities or any(not str(row[0]).startswith("demo-") for row in identities) or (DEMO_PUUID,) not in players:
        raise ValueError("La base de démonstration contient des données non synthétiques. Elle ne sera pas ouverte ni remplacée.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Open an isolated synthetic demo of LoL Analytics")
    parser.add_argument("--port", type=int, default=8502)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    # New fixture generation, without replacing an older demo or personal DB.
    path = root / "data" / "demo-journal-v2.6.db"
    try:
        prepare_demo(path)
    except (ValueError, sqlite3.Error, OSError) as error:
        parser.exit(1, f"{error}\n")
    environment = os.environ.copy()
    environment.update({
        "LOL_ANALYTICS_DB_PATH": str(path), "RIOT_GAME_NAME": DEMO_GAME_NAME,
        "RIOT_TAG_LINE": DEMO_TAG_LINE, "RIOT_PLATFORM_REGION": "EUW1",
        "RIOT_ROUTING_REGION": "EUROPE", "RIOT_API_KEY": "", "LOL_ANALYTICS_DEMO": "1",
    })
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(root / "app.py"),
                    "--server.port", str(args.port), "--server.address", "127.0.0.1",
                    "--browser.gatherUsageStats", "false"], cwd=root, env=environment, check=True)


if __name__ == "__main__":
    main()
