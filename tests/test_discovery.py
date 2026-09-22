from __future__ import annotations

import polars as pl

from analytics.discovery import discover_contexts, promoted_insights


def _games(each: int) -> pl.DataFrame:
    total = each * 2
    return pl.DataFrame({
        "match_id": [str(i) for i in range(total)],
        "game_creation": list(range(total)),
        "duration": [1800] * total,
        "side": ["blue"] * each + ["red"] * each,
        "queue_id": [420] * total,
        "patch": ["16.17"] * total,
        "win": [1] * each + [0] * each,
        "kills": [5] * total, "deaths": [2] * total, "assists": [8] * total,
        "cs_total": [180] * total, "gold_earned": [12000] * total,
        "damage_dealt": [15000] * total, "vision_score": [20] * total,
    })


def test_small_groups_are_never_candidates() -> None:
    assert discover_contexts(_games(4)) == []


def test_five_is_candidate_and_ten_can_be_highlight() -> None:
    candidates = discover_contexts(_games(5))
    assert any(row["context"] == "Side" and not row["promoted"] for row in candidates)
    promoted = promoted_insights(_games(10))
    assert any(row["context"] == "Side" for row in promoted)
    forbidden = ("causes", "makes you win", "guarantees", "improves your chance", "better because", "tu fatigues", "tu tilt")
    assert not any(term in str(promoted).lower() for term in forbidden)
