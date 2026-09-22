"""Offline QA only: generated temporary libraries, no environment or Riot access.

python -m scripts.reliability_benchmark --sizes 500 1000 2500
Prints JSONL metrics; retains no database and overwrites no output file.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import timedelta
import gc
import json
from pathlib import Path
import tempfile
from time import perf_counter
import tracemalloc

import polars as pl

from analytics.ai_bundle import build_ai_bundle
from analytics.context import build_context_dataset
from analytics.explorer import AnalysisFilters, build_analysis_dataset
from analytics.insights import build_insight_bundle
from analytics.overview import champion_summary, get_overview_summary, get_player_matches
from analytics.teammates import teammate_summary, champion_pairings
from core.db import Database
from core.models import RiotAccount, parse_riot_match
from core.timeline import parse_timeline
from scripts.create_demo import _game, DEMO_START, DEMO_PUUID


def synthetic_library(path: Path, size: int, coverage: float = .5, *, dense_timeline=False) -> Database:
    if path.exists():
        raise FileExistsError("QA generator refuses to overwrite an existing database.")
    database = Database(path)
    database.initialize()
    database.upsert_player(RiotAccount(puuid=DEMO_PUUID, game_name="Synthetic QA", tag_line="TEST"), "EUW1", "EUROPE")
    for index in range(size):
        match, timeline = _game(index, DEMO_START + timedelta(minutes=40 * index))
        database.insert_match(parse_riot_match(match))
        if index < int(size * coverage):
            # Bounded frame density; stress cardinality, not a minute-by-minute replay.
            if not dense_timeline:
                timeline["info"]["frames"] = [frame for frame in timeline["info"]["frames"]
                    if frame["timestamp"] in (0, 300_000, 600_000, 900_000, 1_200_000)]
            parsed = parse_timeline(timeline)
            database.save_timeline(parsed.match_id, parsed.frames, parsed.events)
    return database


def measure(size, name, function, counter):
    gc.collect()
    before = counter[0]
    started = perf_counter()
    result = function()
    elapsed = perf_counter() - started
    print(json.dumps({"games": size, "operation": name, "seconds": round(elapsed, 4),
                      "select_queries": counter[0] - before}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=[500, 1000, 2500])
    parser.add_argument("--memory", action="store_true", help="Additional traced ZIP preparation (slower).")
    args = parser.parse_args()
    for size in args.sizes:
        if not 1 <= size <= 2500:
            parser.error("QA size must be 1..2500")
        with tempfile.TemporaryDirectory(prefix="lol-reliability-") as directory:
            database = synthetic_library(Path(directory) / "synthetic.db", size)
            counter = [0]
            original = database.connection
            @contextmanager
            def counted():
                with original() as connection:
                    def trace(sql):
                        if sql.lstrip().upper().startswith("SELECT"):
                            counter[0] += 1
                    connection.set_trace_callback(trace)
                    yield connection
            database.connection = counted
            games = get_player_matches(database, DEMO_PUUID, None)
            measure(size, "Overview", lambda: get_overview_summary(games), counter)
            measure(size, "Champions", lambda: champion_summary(games), counter)
            context = measure(size, "Contexts dataset", lambda: build_context_dataset(games, database, DEMO_PUUID), counter)
            measure(size, "Insights", lambda: build_insight_bundle(context, pl.DataFrame()), counter)
            participants = database.participants_for_player_matches(DEMO_PUUID)
            measure(size, "Contexts teammates", lambda: teammate_summary(context, participants, DEMO_PUUID), counter)
            measure(size, "Contexts pairings", lambda: champion_pairings(context, participants, DEMO_PUUID), counter)
            measure(size, "Explorer", lambda: build_analysis_dataset(context, AnalysisFilters(base_game_limit=None, champion="Shyvana")), counter)
            bundle_fn = lambda: build_ai_bundle(games, database, DEMO_PUUID, "Synthetic QA#TEST", "EUW1", "EUROPE", DEMO_START)
            bundle = measure(size, "AI Export", bundle_fn, counter)
            print(json.dumps({"games": size, "zip_bytes": len(bundle)}), flush=True)
            if args.memory:
                tracemalloc.start()
                bundle_fn()
                _, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                print(json.dumps({"games": size, "export_python_peak_mib": round(peak / 2**20, 2)}), flush=True)


if __name__ == "__main__":
    main()
