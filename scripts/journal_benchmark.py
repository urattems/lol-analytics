"""Synthetic journal preparation, excluding DB generation, browser and network."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
from time import perf_counter

from analytics.context import build_context_dataset
from analytics.history import prepare_history
from analytics.journal import recurring_players, tag_observations
from analytics.overview import get_player_matches
from analytics.share import ShareOptions, prepare_share, share_zip
from core.journal import load_annotations
from scripts.create_demo import DEMO_PUUID
from scripts.reliability_benchmark import synthetic_library


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sizes', type=int, nargs='+', default=[500,1000,2500])
    args = parser.parse_args()
    if any(not 1 <= n <= 2500 for n in args.sizes): parser.error('Sizes must be 1..2500')
    for size in args.sizes:
        with tempfile.TemporaryDirectory(prefix='lol-journal-benchmark-') as directory:
            database = synthetic_library(Path(directory) / 'synthetic.db', size, coverage=.5)
            matches = database.player_matches(DEMO_PUUID)
            with database.connection() as c:
                c.executemany('INSERT INTO match_notes VALUES (?,?,?,?,?)', [(DEMO_PUUID,r['match_id'],f'Synthetic review note {index}: review my next recall.',1,1000) for index,r in enumerate(matches)])
                c.executemany('INSERT INTO match_tags VALUES (?,?,?)', [(DEMO_PUUID,r['match_id'],f'tag-{index%50}') for index,r in enumerate(matches)])
            games = build_context_dataset(get_player_matches(database, DEMO_PUUID, None), database, DEMO_PUUID)
            library = prepare_history(games, database.participants_for_player_matches(DEMO_PUUID), DEMO_PUUID, database.list_players())
            start = perf_counter()
            tags = tag_observations(games, load_annotations(database, DEMO_PUUID))
            tagged = perf_counter()
            people = recurring_players(library, games)
            grouped = perf_counter()
            queries = []
            original = database.connection
            @contextmanager
            def counted():
                with original() as connection:
                    connection.set_trace_callback(lambda sql: queries.append(sql) if sql.lstrip().upper().startswith('SELECT') else None)
                    yield connection
            database.connection = counted
            preview = prepare_share(database, DEMO_PUUID, games['match_id'].to_list(), ShareOptions(checkpoints=True,rosters=True,annotations=True))
            bundle = share_zip(preview, annotations_reviewed=True)
            ended = perf_counter()
            print(json.dumps({'games':size,'tags':len(tags),'recurring_players':len(people),
                'tag_seconds':round(tagged-start,4),'players_seconds':round(grouped-tagged,4),
                'share_seconds':round(ended-grouped,4),'share_selects':len(queries),'zip_bytes':len(bundle)}), flush=True)


if __name__ == '__main__': main()
