from __future__ import annotations

import polars as pl

from analytics.insights import (
    build_insight_bundle,
    duration_preset,
    sample_confidence,
)


def _games(count: int = 40) -> pl.DataFrame:
    rows = []
    durations = (1_100, 1_300, 1_600, 1_900, 2_200)
    queues = (420, 400, 480)
    for index in range(count):
        rows.append(
            {
                "match_id": f"EUW1_{index:03d}",
                "patch": "16.17" if index < count // 2 else "16.16",
                "queue_id": queues[index % len(queues)],
                "duration": durations[index % len(durations)],
                "game_creation": 1_750_000_000_000 + index,
                "champion": "Shyvana",
                "role": "JUNGLE",
                "win": 1 if index % 2 == 0 else 0,
                "kills": 6 + index % 4,
                "deaths": 2 + index % 3,
                "assists": 8,
                "gold_earned": 10_000 + index * 10,
                "cs_total": 150 + index,
                "damage_dealt": 14_000 + index * 20,
                "damage_share": 0.3,
                "vision_score": 20,
                "items": "[1001,3078,0,0,0,0,3340]",
                "side": "blue" if index < count // 2 else "red",
            }
        )
    return pl.from_dicts(rows)


def _timeline(count: int = 40) -> pl.DataFrame:
    rows = []
    for index in range(count):
        state = index % 3
        rows.append(
            {
                "match_id": f"EUW1_{index:03d}",
                "timeline_available": True,
                "gold_10": 5_000 + index,
                "gold_15": 8_000 + index,
                "cs_10": 70,
                "cs_15": 110,
                "gold_diff_10": 600 if state == 0 else 0 if state == 1 else -600,
                "gold_diff_15": 700 if state == 0 else 0 if state == 1 else -700,
                "team_gold_diff_15": 1_500 if state == 0 else 0 if state == 1 else -1_500,
                "deaths_before_10": 1 if index % 2 else 0,
                "first_death_before_10": bool(index % 2),
            }
        )
    return pl.from_dicts(rows)


def test_sample_size_labels_have_documented_boundaries() -> None:
    assert [sample_confidence(n) for n in (1, 4, 5, 9, 10, 24, 25)] == [
        "Très faible", "Très faible", "Limité", "Limité",
        "Modéré", "Modéré", "Solide descriptivement",
    ]


def test_duration_buckets_cover_every_game_once() -> None:
    rows = duration_preset(_games(5))
    assert [row["label"] for row in rows] == ["<20 min", "20–25", "25–30", "30–35", "35+"]
    assert sum(int(row["games"]) for row in rows) == 5


def test_context_presets_side_queue_patch_recent_and_fast_wins() -> None:
    bundle = build_insight_bundle(_games(), _timeline())
    assert {row["label"] for row in bundle["side"]} == {"BLUE SIDE", "RED SIDE"}  # type: ignore[union-attr]
    assert {row["label"] for row in bundle["role"]} == {"JUNGLE"}  # type: ignore[union-attr]
    assert {row["label"] for row in bundle["queue"]} == {  # type: ignore[union-attr]
        "Ranked Solo/Duo", "Normal Draft", "Swiftplay"
    }
    assert {row["label"] for row in bundle["patch"]} == {"16.16", "16.17"}  # type: ignore[union-attr]
    recent = bundle["recent_previous"]
    assert [row["games"] for row in recent] == [20, 20]  # type: ignore[index]
    assert bundle["fast_wins"]["games"] == 4  # type: ignore[index]


def test_timeline_presets_cover_ahead_behind_conversion_comeback_and_first_death() -> None:
    bundle = build_insight_bundle(_games(), _timeline())
    assert bundle["coverage"] == {"available": 40, "selected": 40}
    ahead = bundle["ahead_10"]
    assert [row["label"] for row in ahead] == ["Ahead", "Even", "Behind"]  # type: ignore[index]
    assert sum(int(row["games"]) for row in ahead) == 40  # type: ignore[union-attr]
    assert bundle["conversion"]["games"] == 14  # type: ignore[index]
    assert bundle["comeback"]["games"] == 13  # type: ignore[index]
    first_death = bundle["first_death"]
    assert [row["games"] for row in first_death] == [20, 20]  # type: ignore[index]


def test_timeline_join_keeps_matches_without_timeline() -> None:
    bundle = build_insight_bundle(_games(10), _timeline(3))
    assert bundle["coverage"] == {"available": 3, "selected": 10}
    assert bundle["games"].height == 10  # type: ignore[union-attr]
