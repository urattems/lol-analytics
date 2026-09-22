from __future__ import annotations

import polars as pl

from analytics.explorer import (
    AnalysisFilters,
    ExplorerSelection,
    analysis_summary,
    build_analysis_dataset,
)


def _games() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "match_id": ["6", "5", "4", "3", "2", "1"],
            "game_creation": [600, 500, 400, 300, 200, 100],
            "champion": ["Lulu", "Shyvana", "Shyvana", "Leona", "Shyvana", "Lulu"],
            "role": ["UTILITY", "JUNGLE", "JUNGLE", "UTILITY", "TOP", "UTILITY"],
            "queue_id": [420, 420, 440, 420, 420, 440],
            "patch": ["16.17", "16.17", "16.17", "16.16", "16.16", "16.16"],
            "side": ["blue", "red", "blue", "red", "blue", "red"],
            "duration": [1_800, 1_500, 1_200, 900, 600, 300],
            "win": [1, 0, 1, 0, 1, 1],
            "kills": [1, 8, 5, 2, 4, 0],
            "deaths": [2, 4, 1, 5, 0, 0],
            "assists": [12, 6, 8, 10, 6, 10],
            "cs_total": [20, 180, 140, 25, 80, None],
            "gold_earned": [9_000, 10_000, 8_000, 6_000, 5_000, 3_000],
            "damage_dealt": [4_000, 12_000, 9_000, 5_000, 6_000, None],
            "vision_score": [40, 20, 15, 30, 5, 10],
        }
    )


def test_base_limit_is_applied_before_champion_filter() -> None:
    selection = build_analysis_dataset(
        _games(), AnalysisFilters(base_game_limit=2, champion="Shyvana")
    )
    assert selection.base["match_id"].to_list() == ["6", "5"]
    assert selection.filtered["match_id"].to_list() == ["5"]


def test_each_context_filter_is_supported() -> None:
    games = _games()
    assert build_analysis_dataset(games, AnalysisFilters(role="TOP")).filtered.height == 1
    assert build_analysis_dataset(games, AnalysisFilters(queue_id=440)).filtered.height == 2
    assert build_analysis_dataset(games, AnalysisFilters(patch="16.16")).filtered.height == 3
    assert build_analysis_dataset(games, AnalysisFilters(side="blue")).filtered.height == 3
    assert build_analysis_dataset(games, AnalysisFilters(win=True)).filtered["win"].to_list() == [1, 1, 1, 1]
    duration = build_analysis_dataset(
        games, AnalysisFilters(min_duration=900, max_duration=1_200)
    ).filtered
    assert duration["match_id"].to_list() == ["4", "3"]


def test_combined_filters_and_no_result() -> None:
    combined = build_analysis_dataset(
        _games(),
        AnalysisFilters(champion="Shyvana", role="JUNGLE", queue_id=420, side="red"),
    ).filtered
    assert combined["match_id"].to_list() == ["5"]
    assert build_analysis_dataset(
        _games(), AnalysisFilters(champion="Leona", role="JUNGLE")
    ).filtered.is_empty()


def test_limit_larger_than_available_and_order_are_safe() -> None:
    selection = build_analysis_dataset(_games().reverse(), AnalysisFilters(base_game_limit=100))
    assert selection.base.height == 6
    assert selection.filtered["match_id"].to_list() == ["6", "5", "4", "3", "2", "1"]


def test_invalid_duration_range_is_normalized() -> None:
    filters = AnalysisFilters(min_duration=1_500, max_duration=600)
    assert filters.min_duration == 600
    assert filters.max_duration == 1_500
    assert build_analysis_dataset(_games(), filters).filtered.height == 4


def test_analysis_summary_has_extended_metrics_and_missing_values() -> None:
    summary = analysis_summary(_games())
    assert summary["games"] == 6
    assert summary["gold_per_minute"] is not None
    assert summary["vision_per_minute"] is not None
    empty = analysis_summary(_games().head(0))
    assert empty["games"] == 0
    assert empty["gold_per_minute"] == 0.0


def test_short_games_are_excluded_by_default_and_299_is_included_on_opt_in() -> None:
    games = _games().with_columns(
        pl.when(pl.col("match_id") == "6")
        .then(pl.lit(299))
        .otherwise(pl.col("duration"))
        .alias("duration")
    )
    included = build_analysis_dataset(games, AnalysisFilters(base_game_limit=None, include_short_games=True))
    excluded = build_analysis_dataset(
        games,
        AnalysisFilters(base_game_limit=None),
    )

    assert included.filtered.height == 6
    assert excluded.filtered.height == 5
    assert "6" not in excluded.filtered["match_id"].to_list()
    assert "1" in excluded.filtered["match_id"].to_list()  # exactly 300 seconds
    assert analysis_summary(excluded.filtered)["winrate"] == 60.0
    assert excluded.filters.active_filters()["include_short_games"] is False


def test_timeline_and_context_filters_share_one_selection_contract() -> None:
    games = _games().with_columns(
        pl.Series("timeline_available", [True, True, False, True, True, False]),
        pl.Series("gold_diff_10", [700, 500, None, -200, 900, None]),
        pl.Series("gold_diff_15", [900, 800, None, -500, 1100, None]),
        pl.Series("first_death_before_10", [False, False, None, True, False, None]),
        pl.Series("opponent_champion", ["Nidalee", "Nocturne", None, "Lux", "Garen", None]),
        pl.Series("session_game_bucket", ["1", "2", "1", "3", "4+", "1"]),
        pl.Series("first_dragon", ["ours", "ours", None, "theirs", "ours", None]),
        pl.Series("trajectory", ["AHEAD → AHEAD → AHEAD", "AHEAD → EVEN → AHEAD", None, "BEHIND → EVEN → AHEAD", "AHEAD → AHEAD → AHEAD", None]),
    )
    selection = ExplorerSelection(
        base_game_limit=None,
        champion="Shyvana",
        timeline_requirement="available",
        gold_diff_10_min=400,
        gold_diff_15_max=1000,
        first_death_before_10=False,
        opponent_champion="Nocturne",
        session_game_number="2",
        first_dragon="ours",
        trajectory="AHEAD → EVEN → AHEAD",
    )
    assert AnalysisFilters is ExplorerSelection
    assert build_analysis_dataset(games, selection).filtered["match_id"].to_list() == ["5"]
