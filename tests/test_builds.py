from __future__ import annotations

import polars as pl

from analytics.builds import (
    build_summary,
    canonical_build_key,
    compare_builds,
    filter_build_dataset,
    parse_item_ids,
    purchase_sequence_summary,
)


def _games() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "items": ["[3078, 1001, 0]", "[0,1001,3078]", "[3153]"],
            "champion": ["Shyvana", "Shyvana", "Lulu"],
            "role": ["JUNGLE", "JUNGLE", "UTILITY"],
            "win": [1, 0, 1],
            "kills": [8, 2, 1],
            "deaths": [2, 4, 0],
            "assists": [10, 4, 12],
            "duration": [1_800, 1_200, 600],
            "cs_total": [210, 120, 10],
            "gold_earned": [12_000, 8_000, 4_000],
            "damage_dealt": [18_000, 8_000, 3_000],
        }
    )


def test_item_json_removes_empty_slots_and_tolerates_invalid_data() -> None:
    assert parse_item_ids("[1001, 0, 3078]") == [1001, 3078]
    assert parse_item_ids("not json") == []
    assert parse_item_ids(None) == []


def test_build_summary_accepts_late_values_and_ignores_opaque_context():
    games = pl.concat([_games().head(1)] * 105).with_columns(
        pl.Series('late_context', [None] * 100 + [10] * 5),
        pl.Series('gold_earned', [None] * 100 + [12000] * 5),
    )
    result = build_summary(games).row(0, named=True)
    assert result['games'] == 105
    assert result['gold_per_minute'] == 400


def test_canonical_key_ignores_slot_order() -> None:
    assert canonical_build_key("[3078, 0, 1001]") == canonical_build_key([1001, 3078])


def test_equivalent_builds_group_with_correct_sample_and_winrate() -> None:
    summary = build_summary(_games())
    grouped = summary.filter(pl.col("build_key") == "[1001,3078]").row(0, named=True)
    assert grouped["games"] == 2
    assert grouped["wins"] == 1
    assert grouped["losses"] == 1
    assert grouped["winrate"] == 50.0
    assert grouped["items"] == [1001, 3078]


def test_single_item_and_empty_dataset_are_supported() -> None:
    summary = build_summary(_games())
    assert summary.filter(pl.col("build_key") == "[3153]")["games"].item() == 1
    assert build_summary(_games().head(0)).is_empty()


def test_build_filters_are_combined() -> None:
    filtered = filter_build_dataset(_games(), champion="Shyvana", role="JUNGLE")
    assert filtered.height == 2
    assert filter_build_dataset(_games(), champion="Lulu", role="JUNGLE").is_empty()


def test_two_builds_can_be_selected_without_ranking_them() -> None:
    summary = build_summary(_games())
    keys = summary["build_key"].to_list()
    build_a, build_b = compare_builds(summary, keys[0], keys[1])
    assert build_a is not None
    assert build_b is not None
    assert {build_a["games"], build_b["games"]} == {1, 2}


def test_different_trinkets_group_as_one_main_build() -> None:
    games = _games().head(2).with_columns(
        pl.Series(
            "items",
            [
                "[3078,1001,0,0,0,0,3340]",
                "[1001,3078,0,0,0,0,3364]",
            ],
        )
    )
    summary = build_summary(games)

    assert canonical_build_key(games["items"][0]) == canonical_build_key(games["items"][1])
    assert summary.height == 1
    assert summary.row(0, named=True)["items"] == [1001, 3078]
    assert summary.row(0, named=True)["games"] == 2


def test_real_purchase_sequences_remain_order_sensitive() -> None:
    rows = [
        {"win": 1, "purchases": [
            {"event_type": "ITEM_PURCHASED", "item_id": 1001, "timestamp_ms": 100_000},
            {"event_type": "ITEM_PURCHASED", "item_id": 3078, "timestamp_ms": 500_000},
        ]},
        {"win": 0, "purchases": [
            {"event_type": "ITEM_PURCHASED", "item_id": 3078, "timestamp_ms": 200_000},
            {"event_type": "ITEM_PURCHASED", "item_id": 1001, "timestamp_ms": 600_000},
        ]},
        {"win": 1, "purchases": [
            {"event_type": "ITEM_PURCHASED", "item_id": 1001, "timestamp_ms": 120_000},
            {"event_type": "ITEM_SOLD", "item_id": 1001, "timestamp_ms": 300_000},
            {"event_type": "ITEM_PURCHASED", "item_id": 3078, "timestamp_ms": 520_000},
        ]},
    ]
    result = purchase_sequence_summary(rows)
    assert len(result) == 2
    assert result[0]["sequence"] == [1001, 3078]
    assert result[0]["games"] == 2
    assert result[0]["average_timings_ms"] == [110_000, 510_000]
