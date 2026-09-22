"""Local session preparation timing; synthetic data only, no settings or API.

python -m scripts.session_review_benchmark --sizes 500 1000 2500
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
from time import perf_counter

import polars as pl

from analytics.context import build_context_dataset
from analytics.overview import get_player_matches
from analytics.session_review import prepare_review_history, build_session_review
from analytics.session_export import build_session_package
from scripts.reliability_benchmark import synthetic_library
from scripts.create_demo import DEMO_PUUID


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=[500, 1000, 2500])
    args = parser.parse_args()
    for size in args.sizes:
        if not 8 <= size <= 2500:
            parser.error("Sizes must be 8..2500")
        with tempfile.TemporaryDirectory(prefix="lol-session-review-") as directory:
            db = synthetic_library(Path(directory) / "synthetic.db", size, .5)
            count = [0]
            original = db.connection
            @contextmanager
            def counted():
                with original() as connection:
                    connection.set_trace_callback(lambda sql: count.__setitem__(0, count[0] + int(sql.lstrip().upper().startswith("SELECT"))))
                    yield connection
            db.connection = counted
            started = perf_counter()
            context = build_context_dataset(get_player_matches(db, DEMO_PUUID, None), db, DEMO_PUUID)
            cold = perf_counter() - started
            queries = count[0]
            # The shared scale fixture is consecutive. Spread into four-game
            # daily sessions, without rewriting SQLite, for meaningful baselines.
            context = context.sort(["game_creation", "match_id"]).with_row_index("qa_index").with_columns(
                (1_700_000_000_000 + (pl.col("qa_index").cast(pl.Int64) // 4) * 86_400_000 + (pl.col("qa_index") % 4) * 40 * 60_000).alias("game_creation")
            ).drop("qa_index")
            started = perf_counter()
            history = prepare_review_history(context)
            prepared = perf_counter() - started
            started = perf_counter()
            review = build_session_review(history)
            review_seconds = perf_counter() - started
            started = perf_counter()
            package = build_session_package(review, "Synthetic QA#TEST")
            export_seconds = perf_counter() - started
            print(json.dumps({"games": size, "cold_context_seconds": round(cold, 4), "cold_select_queries": queries,
                "prepare_seconds": round(prepared, 4), "review_seconds": round(review_seconds, 4),
                "session_export_seconds": round(export_seconds, 4), "review_extra_queries": count[0] - queries,
                "session_games": review["summary"]["games"], "zip_bytes": len(package)}), flush=True)


if __name__ == "__main__":
    main()
