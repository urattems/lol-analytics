"""Offline progression preparation timings, excluding browser/network and SQLite generation."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
from time import perf_counter

from analytics.context import build_context_dataset
from analytics.overview import get_player_matches
from analytics.progression import ProgressionScope, compare_windows, compare_metrics, home_observation
from analytics.matchup_experience import matchup_observations
from scripts.create_demo import DEMO_PUUID
from scripts.reliability_benchmark import synthetic_library


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sizes', type=int, nargs='+', default=[500, 1000, 2500])
    args = parser.parse_args()
    if any(not 1 <= size <= 2500 for size in args.sizes):
        parser.error('Sizes must be 1..2500')
    for size in args.sizes:
        with tempfile.TemporaryDirectory(prefix='lol-progression-benchmark-') as folder:
            database = synthetic_library(Path(folder) / 'synthetic.db', size, coverage=.5)
            queries = []
            original = database.connection
            @contextmanager
            def counted():
                with original() as connection:
                    connection.set_trace_callback(lambda sql: queries.append(sql) if sql.lstrip().upper().startswith('SELECT') else None)
                    yield connection
            database.connection = counted
            start = perf_counter()
            games = build_context_dataset(get_player_matches(database, DEMO_PUUID, None), database, DEMO_PUUID)
            loaded = perf_counter()
            comparison = compare_windows(games, ProgressionScope(patch_policy='mixed', recent_count=20, previous_count=50))
            compare_metrics(comparison)
            compared = perf_counter()
            home_observation(games)
            home = perf_counter()
            matchups = matchup_observations(games)
            end = perf_counter()
            assert not set(comparison.recent['match_id']) & set(comparison.previous['match_id'])
            print(json.dumps({'games': size, 'coverage': .5, 'context_selects': len(queries),
                'context_seconds': round(loaded-start, 4), 'compare_20_50_seconds': round(compared-loaded, 4),
                'home_seconds': round(home-compared, 4), 'matchups_seconds': round(end-home, 4),
                'opponents': len(matchups)}), flush=True)


if __name__ == '__main__':
    main()
