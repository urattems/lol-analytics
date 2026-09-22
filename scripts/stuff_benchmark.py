"""Offline dense Timeline benchmark; only temporary invented data, no .env reads.

python -m scripts.stuff_benchmark --sizes 500 1000 2500
Measures batched loading + timing/path aggregation, not browser rendering or RSS.
"""
import argparse
from contextlib import contextmanager
from pathlib import Path
import tempfile
from time import perf_counter
import json

from analytics.stuff import load_stuff_matches, path_groups, timing_groups
from core.item_catalog import ItemCatalog, ItemDefinition
from scripts.create_demo import DEMO_PUUID
from scripts.reliability_benchmark import synthetic_library


def synthetic_catalogs(size=72):
    # Invented recipes sufficient for this generator, NOT Riot metadata or a
    # classification dataset for real games. Every measured fact stays local.
    ids = (3115, 3089, 3137, 3078, 3748, 3053, 3190, 3109, 3075, 3118, 3157, 3031, 3085, 3036, 3071)
    items = {i: ItemDefinition(i, f'Synthetic {i}', 3000, 1000, True, True, True, False, (), (1036,), ()) for i in ids}
    items[1036] = ItemDefinition(1036, 'Synthetic component', 350, 350, True, True, True, False, (), (), ())
    items[1001] = ItemDefinition(1001, 'Synthetic base boots', 300, 300, True, True, True, False, ('Boots',), (), ())
    items[3047] = ItemDefinition(3047, 'Synthetic T2', 1100, 800, True, True, True, False, ('Boots',), (1001,), ())
    patches = [f'16.{16 + index}' for index in range((size + 35) // 36)]
    return {p: ItemCatalog(p, p + '.1', 0, items) for p in patches}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sizes', type=int, nargs='+', default=[500, 1000, 2500])
    args = parser.parse_args()
    if any(not 1 <= size <= 2500 for size in args.sizes):
        parser.error('Sizes must be 1..2500')
    for size in args.sizes:
        with tempfile.TemporaryDirectory(prefix='lol-stuff-benchmark-') as directory:
            database = synthetic_library(Path(directory) / 'synthetic.db', size, coverage=1, dense_timeline=True)
            queries = []
            original = database.connection
            @contextmanager
            def counted():
                with original() as c:
                    c.set_trace_callback(lambda sql: queries.append(sql) if sql.lstrip().upper().startswith('SELECT') else None)
                    yield c
            database.connection = counted
            started = perf_counter()
            facts = list(load_stuff_matches(database, DEMO_PUUID, synthetic_catalogs(size)).values())
            loaded = perf_counter()
            groups = [timing_groups(facts, slot=slot) for slot in (0, 1, 2, 3)]
            paths = path_groups(facts)
            ended = perf_counter()
            assert len(facts) == size and all(f.eligible for f in facts), 'Fixture unexpectedly unqualified'
            assert len(queries) == 4
            print(json.dumps({'games': size, 'timeline_coverage': 1, 'minute_frames': True,
                'load_seconds': round(loaded-started, 4), 'aggregate_seconds': round(ended-loaded, 4),
                'select_queries': len(queries), 'timing_groups': sum(map(len, groups)), 'paths': len(paths)}), flush=True)


if __name__ == '__main__':
    main()
