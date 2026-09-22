from __future__ import annotations

import polars as pl

from analytics.consistency import consistency_summary
from analytics.personal_meta import champion_frequency, champion_trends, role_distribution


def _games() -> pl.DataFrame:
    return pl.DataFrame({
        "match_id": ["1", "2", "3", "4"], "game_creation": [1_700_000_000_000] * 4,
        "patch": ["16.16", "16.16", "16.17", "16.17"],
        "champion": ["Shyvana", "Ahri", "Shyvana", "Shyvana"],
        "role": ["JUNGLE", "MIDDLE", "JUNGLE", "JUNGLE"],
        "duration": [1800] * 4, "win": [1, 0, 1, 0],
        "kills": [8, 2, 4, 6], "deaths": [2, 4, 2, 3], "assists": [8, 5, 6, 9],
        "cs_total": [180, 150, 170, 190], "gold_earned": [12000, 10000, 11000, 13000],
        "damage_dealt": [15000, 12000, 14000, 16000], "vision_score": [20] * 4,
        "gold_diff_15": [100, -300, 200, 400],
    })


def test_personal_meta_frequency_role_and_trends() -> None:
    frequency = champion_frequency(_games(), "patch")
    assert any(row["period"] == "16.17" and row["champion"] == "Shyvana" and row["pick_rate"] == 100 for row in frequency)
    roles = role_distribution(_games(), "patch")
    assert any(row["role"] == "JUNGLE" for row in roles)
    trends = champion_trends(_games(), "Shyvana")
    assert [row["games"] for row in trends] == [1, 2]


def test_consistency_has_median_quartiles_and_iqr() -> None:
    rows = consistency_summary(_games())
    gold = next(row for row in rows if row["metric"] == "Gold Diff @15")
    assert gold["median"] == 150.0
    assert gold["p25"] is not None and gold["p75"] is not None
    assert gold["iqr"] == gold["p75"] - gold["p25"]
