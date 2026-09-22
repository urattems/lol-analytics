"""History preparation benchmark. Synthetic temporary DBs; no .env/API.

python -m scripts.history_benchmark --sizes 500 1000 2500
"""
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter
import argparse
import json
import tempfile

from analytics.ai_bundle import build_ai_bundle
from analytics.context import build_context_dataset
from analytics.explorer import ExplorerSelection
from analytics.history import prepare_history
from analytics.overview import get_player_matches
from analytics.session_review import build_session_review
from analytics.teammates import teammate_summary
from scripts.create_demo import DEMO_PUUID
from scripts.reliability_benchmark import synthetic_library
from analytics.matchup_cards import matchup_cards


def benchmark(size):
    with tempfile.TemporaryDirectory(prefix='lol-history-qa-') as directory:
        database = synthetic_library(Path(directory) / 'synthetic.db', size, .5)
        selects = [0]
        original = database.connection
        @contextmanager
        def counted():
            with original() as connection:
                connection.set_trace_callback(lambda sql: selects.__setitem__(0, selects[0] + int(sql.lstrip().upper().startswith('SELECT'))))
                yield connection
        database.connection = counted
        result = {'games': size}
        def measure(name, callback):
            before, queries = perf_counter(), selects[0]
            value = callback()
            result[name + '_seconds'] = round(perf_counter() - before, 5)
            result[name + '_selects'] = selects[0] - queries
            return value
        context = measure('cold_context', lambda: build_context_dataset(get_player_matches(database, DEMO_PUUID, None), database, DEMO_PUUID))
        participants = measure('rosters', lambda: database.participants_for_player_matches(DEMO_PUUID))
        profiles = database.list_players()
        library = measure('history_prepare', lambda: prepare_history(context, participants, DEMO_PUUID, profiles))
        selected = measure('filter', lambda: library.select(ExplorerSelection(base_game_limit=None)))
        first = measure('first_20', lambda: library.batch(selected))
        names = measure('roster_200_labels', lambda: [p.identity.local_label for card in first for p in card.players])
        result['visible_roster_labels'] = len(names)
        second = measure('next_20', lambda: library.batch(selected, 20))
        measure('teammates', lambda: teammate_summary(selected, participants, DEMO_PUUID))
        measure('matchup_cards', lambda: matchup_cards(selected))
        measure('session_review', lambda: build_session_review(library.games))
        bundle = measure('full_zip', lambda: build_ai_bundle(context, database, DEMO_PUUID, 'Synthetic QA#TEST', 'EUW1', 'EUROPE'))
        result.update(first_cards=len(first), next_cards=len(second), zip_bytes=len(bundle))
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sizes', type=int, nargs='+', default=[500, 1000, 2500])
    args = parser.parse_args()
    if any(not 40 <= size <= 2500 for size in args.sizes):
        parser.error('Sizes must be between 40 and 2500.')
    for size in args.sizes:
        print(json.dumps(benchmark(size)), flush=True)


if __name__ == '__main__':
    main()
