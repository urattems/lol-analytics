from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import polars as pl

from analytics.explorer import AnalysisFilters, analysis_summary
from analytics.exports import (
    build_export_payload,
    build_full_export_payload,
    export_filename,
    export_json,
    export_markdown,
)
from core.static_data import ItemStaticData


FIXED_TIME = datetime(2026, 9, 3, 21, 42, tzinfo=timezone(timedelta(hours=2)))


def _games() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "match_id": ["EUW1_2", "EUW1_1"],
            "patch": ["16.17", "16.17"],
            "queue_id": [420, 440],
            "duration": [1_800, 1_200],
            "game_creation": [1_757_000_000_000, 1_756_000_000_000],
            "champion": ["Shyvana", "Lulu"],
            "role": ["JUNGLE", "UTILITY"],
            "win": [1, 0],
            "kills": [8, 1],
            "deaths": [2, 0],
            "assists": [10, 12],
            "gold_earned": [12_000, 8_000],
            "cs_total": [180, None],
            "damage_dealt": [15_000, 5_000],
            "damage_share": [0.32, None],
            "vision_score": [24, 40],
            "items": ["[1001, 3078, 0]", "[3158, 0, 0]"],
            "side": ["blue", "red"],
        }
    )


def _payload(filters: AnalysisFilters | None = None) -> dict[str, object]:
    return build_export_payload(
        _games(),
        filters or AnalysisFilters(base_game_limit=20),
        "Étoile#EUW",
        "EUW1",
        "EUROPE",
        FIXED_TIME,
    )


def test_json_is_parseable_utf8_and_has_stable_schema() -> None:
    document = export_json(_payload())
    decoded = json.loads(document)
    assert decoded["schema_version"] == "2.1"
    assert decoded["generated_at"] == "2026-09-03T21:42:00+02:00"
    assert decoded["player"] == {
        "riot_id": "Étoile#EUW",
        "platform_region": "EUW1",
        "routing_region": "EUROPE",
    }
    assert "Étoile" in document
    assert "NaN" not in document


def test_counts_summary_champions_and_matches_are_consistent() -> None:
    payload = _payload()
    summary = payload["summary"]
    selection = payload["selection"]
    champions = payload["champions"]
    matches = payload["matches"]
    assert isinstance(summary, dict)
    assert isinstance(selection, dict)
    assert isinstance(champions, list)
    assert isinstance(matches, list)
    assert summary == {
        "games": 2,
        "wins": 1,
        "losses": 1,
        "winrate": 50.0,
        "kda": 11.0,
        "cs_per_min": 6.0,
        "gold_per_min": 400.0,
        "damage_per_min": 375.0,
        "vision_per_min": 1.4,
    }
    assert selection["games_in_base_window"] == 2
    assert selection["games_after_filters"] == 2
    assert len(champions) == 2
    assert champions[0]["champion"] == "Lulu"
    assert champions[0]["games"] == 1
    assert len(matches) == 2
    assert matches[0]["items"] == [1001, 3078]
    assert matches[0]["trinket"] is None
    assert matches[0]["queue_name"] == "Ranked Solo/Duo"
    assert matches[1]["cs"] is None
    assert matches[1]["cs_per_min"] is None


def test_summary_uses_the_dashboard_formulas() -> None:
    payload = _payload()
    expected = analysis_summary(_games())
    summary = payload["summary"]
    assert isinstance(summary, dict)
    assert summary["winrate"] == expected["winrate"]
    assert summary["kda"] == expected["kda"]
    assert summary["cs_per_min"] == expected["cs_per_minute"]
    assert summary["damage_per_min"] == expected["damage_per_minute"]


def test_filtered_selection_reuses_base_window_then_filter_semantics() -> None:
    payload = _payload(AnalysisFilters(base_game_limit=1, champion="Shyvana", side="blue"))
    selection = payload["selection"]
    assert isinstance(selection, dict)
    assert selection["base_game_limit"] == 1
    assert selection["games_in_base_window"] == 1
    assert selection["games_after_filters"] == 1
    assert selection["filters"] == {"champion": "Shyvana", "side": "blue", "include_short_games": False}
    assert len(payload["matches"]) == 1  # type: ignore[arg-type]


def test_export_declares_short_game_exclusion() -> None:
    games = _games().with_columns(
        pl.when(pl.col("match_id") == "EUW1_1")
        .then(pl.lit(299))
        .otherwise(pl.col("duration"))
        .alias("duration")
    )
    payload = build_export_payload(
        games,
        AnalysisFilters(base_game_limit=20, include_short_games=False),
        "Étoile#EUW",
        "EUW1",
        "EUROPE",
        FIXED_TIME,
    )
    selection = payload["selection"]
    assert isinstance(selection, dict)
    assert selection["filters"] == {"include_short_games": False}
    assert selection["games_after_filters"] == 1


def test_dates_are_iso_8601_and_null_values_serialize_as_null() -> None:
    decoded = json.loads(export_json(_payload()))
    datetime.fromisoformat(decoded["matches"][0]["date"])
    datetime.fromisoformat(decoded["selection"]["date_range"]["from"])
    assert decoded["matches"][1]["damage_share"] is None
    assert '"damage_share": null' in export_json(_payload())


def test_empty_dataset_produces_valid_documents() -> None:
    empty = _games().head(0)
    payload = build_export_payload(
        empty,
        AnalysisFilters(base_game_limit=20),
        "Étoile#EUW",
        "EUW1",
        "EUROPE",
        FIXED_TIME,
    )
    decoded = json.loads(export_json(payload))
    assert decoded["summary"]["games"] == 0
    assert decoded["champions"] == []
    assert decoded["matches"] == []
    assert decoded["selection"]["date_range"] == {"from": None, "to": None}
    assert "Aucune partie dans cette sélection." in export_markdown(payload)


def test_markdown_is_factual_complete_and_contains_no_secret() -> None:
    document = export_markdown(_payload())
    assert document
    assert "# LoL Analytics Export" in document
    assert "## Export metadata" in document
    assert "## Global Summary" in document
    assert "## Champion Summary" in document
    assert "## Match Details" in document
    assert "relation causale" in document
    lowered = document.lower()
    for forbidden in ("rgapi-", "riot_api_key", "tu devrais", "build est mauvais", "tu es meilleur"):
        assert forbidden not in lowered


def test_filename_is_sanitized() -> None:
    assert export_filename("Étoile / Test#EUW", 2, ".json", FIXED_TIME) == (
        "lol_analytics_etoile_test_2_games_2026-09-03.json"
    )


def test_item_resolver_adds_localized_names_without_changing_ids() -> None:
    payload = build_export_payload(
        _games(),
        AnalysisFilters(base_game_limit=1),
        "Étoile#EUW",
        "EUW1",
        "EUROPE",
        FIXED_TIME,
        lambda item_id: ItemStaticData(item_id, f"Objet {item_id}", None),
    )
    matches = payload["matches"]
    assert isinstance(matches, list)
    assert matches[0]["items"] == [
        {"id": 1001, "name": "Objet 1001"},
        {"id": 3078, "name": "Objet 3078"},
    ]
    assert "Objet 1001 (1001)" in export_markdown(payload)


def test_full_export_ignores_hidden_filters_and_keeps_every_duration() -> None:
    games = pl.concat(
        [
            _games().head(1).with_columns(
                pl.lit(f"EUW1_{index}").alias("match_id"),
                pl.lit(duration).alias("duration"),
                pl.lit(1_750_000_000_000 + index).alias("game_creation"),
            )
            for index, duration in enumerate((60, 900, 1500, 2700, 3600), start=10)
        ]
    )
    # An Explorer selection may exist elsewhere; this API has no filter input by design.
    payload = build_full_export_payload(
        games, "Étoile#EUW", "EUW1", "EUROPE", FIXED_TIME
    )
    selection = payload["selection"]
    assert isinstance(selection, dict)
    assert selection["mode"] == "full"
    assert selection["filters"] == {}
    assert selection["local_games"] == 5
    assert selection["games_after_filters"] == 5
    assert len(payload["matches"]) == 5  # type: ignore[arg-type]
    assert sum(bool(match["short_game"]) for match in payload["matches"]) == 1  # type: ignore[index]
    assert {match["duration_seconds"] for match in payload["matches"]} == {60, 900, 1500, 2700, 3600}  # type: ignore[index]


def test_items_and_trinket_are_exported_separately() -> None:
    games = _games().head(1).with_columns(
        pl.lit("[1001,3078,0,0,0,0,3340]").alias("items")
    )
    payload = build_full_export_payload(
        games, "Étoile#EUW", "EUW1", "EUROPE", FIXED_TIME
    )
    match = payload["matches"][0]  # type: ignore[index]
    assert match["items"] == [1001, 3078]
    assert match["trinket"] == 3340


def test_custom_export_is_exact_explorer_selection_with_context_filters() -> None:
    games = _games().with_columns(
        pl.Series("timeline_available", [True, False]),
        pl.Series("gold_diff_10", [600, None]),
        pl.Series("gold_diff_15", [900, None]),
        pl.Series("first_death_before_10", [False, None]),
        pl.Series("opponent_champion", ["Nocturne", None]),
        pl.Series("session_game_bucket", ["2", "1"]),
        pl.Series("first_dragon", ["ours", None]),
        pl.Series("trajectory", ["AHEAD → EVEN → AHEAD", None]),
    )
    filters = AnalysisFilters(
        base_game_limit=None, timeline_requirement="available",
        gold_diff_10_min=500, gold_diff_15_max=1000,
        first_death_before_10=False, opponent_champion="Nocturne",
        session_game_number="2", first_dragon="ours",
        trajectory="AHEAD → EVEN → AHEAD",
    )
    expected = games.filter(pl.col("match_id") == "EUW1_2")
    payload = build_export_payload(games, filters, "Étoile#EUW", "EUW1", "EUROPE", FIXED_TIME)
    assert [row["match_id"] for row in payload["matches"]] == expected["match_id"].to_list()
