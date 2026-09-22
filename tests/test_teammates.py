from __future__ import annotations

import polars as pl

from analytics.teammates import (
    champion_pairings,
    enrich_teammate_context,
    solo_vs_known,
    squad_summary,
    teammate_occurrences,
    teammate_summary,
)


def _games() -> pl.DataFrame:
    return pl.DataFrame({
        "match_id": ["m1", "m2", "m3", "m4"],
        "game_creation": [1, 2, 3, 4],
        "duration": [1800] * 4,
        "champion": ["Shyvana"] * 4,
        "win": [1, 0, 1, 0],
        "kills": [5] * 4, "deaths": [2] * 4, "assists": [8] * 4,
        "cs_total": [180] * 4, "gold_earned": [12000] * 4,
        "damage_dealt": [15000] * 4, "vision_score": [20] * 4,
        "gold_diff_15": [100] * 4, "cs_diff_15": [2] * 4,
        "team_gold_diff_15": [300] * 4,
    })


def _participants() -> list[dict[str, object]]:
    rows = []
    for match_id in ("m1", "m2", "m3"):
        rows.extend([
            {"match_id": match_id, "puuid": "player", "side": "blue", "champion": "Shyvana", "game_creation": int(match_id[-1])},
            {"match_id": match_id, "puuid": "a", "side": "blue", "champion": "Leona", "teammate_game_name": "Alice", "teammate_tag_line": "EUW", "game_creation": int(match_id[-1])},
            {"match_id": match_id, "puuid": "b", "side": "blue", "champion": "Lulu", "game_creation": int(match_id[-1])},
            {"match_id": match_id, "puuid": "c", "side": "blue", "champion": "Garen", "game_creation": int(match_id[-1])},
            {"match_id": match_id, "puuid": "d", "side": "blue", "champion": "Ahri", "game_creation": int(match_id[-1])},
            {"match_id": match_id, "puuid": "enemy", "side": "red", "champion": "Nocturne", "game_creation": int(match_id[-1])},
        ])
    rows.extend([
        {"match_id": "m4", "puuid": "player", "side": "blue", "champion": "Shyvana", "game_creation": 4},
        {"match_id": "m4", "puuid": "oneoff", "side": "blue", "champion": "Lux", "game_creation": 4},
    ])
    return rows


def test_recurring_identity_real_name_and_stable_fallback() -> None:
    rows = teammate_summary(_games(), _participants(), "player")
    alice = next(row for row in rows if row["teammate_puuid"] == "a")
    fallback = next(row for row in rows if row["teammate_puuid"] == "b")
    assert alice["display_name"] == "Alice#EUW"
    assert str(fallback["display_name"]).startswith("teammate_")
    assert (alice["games"], alice["first_game"], alice["last_game"]) == (3, 1, 3)


def test_duos_trios_and_four_member_squads_are_detected() -> None:
    squads = squad_summary(_games(), _participants(), "player")
    assert {row["size"] for row in squads} == {2, 3, 4}
    assert any(row["size"] == 4 and row["games"] == 3 for row in squads)


def test_solo_known_context_and_champion_pairing() -> None:
    enriched = enrich_teammate_context(_games(), _participants(), "player")
    assert enriched["with_recurring_teammate"].to_list() == [True, True, True, False]
    contexts = solo_vs_known(enriched)
    assert {row["games"] for row in contexts} == {1, 3}
    pairings = champion_pairings(_games(), _participants(), "player")
    assert any(row["display_name"] == "Alice#EUW" and row["teammate_champion"] == "Leona" for row in pairings)


def test_invalid_teammate_identity_never_creates_a_recurring_ghost() -> None:
    rows = _participants()
    for match_id in ("m1", "m2", "m3"):
        for identity in (None, "", " ", "null", "None", 42, "bad value", "bad\nvalue"):
            rows.append({"match_id": match_id, "puuid": identity, "side": "blue"})
    summaries = teammate_summary(_games(), rows, "player")
    assert {row["teammate_puuid"] for row in summaries} == {"a", "b", "c", "d", "oneoff"}
    assert len(teammate_occurrences(rows, "player")) == 13


def test_unknown_side_and_duplicate_teammates_do_not_form_squads() -> None:
    unknown = [
        {"match_id": "m1", "puuid": "player", "side": None},
        {"match_id": "m1", "puuid": "a", "side": None},
    ]
    assert teammate_occurrences(unknown, "player") == []
    rows = _participants()
    duplicate = next(row for row in rows if row.get("puuid") == "a")
    rows.extend([duplicate, duplicate])
    assert len(teammate_occurrences(rows, "player")) == 13
    assert all(len(set(row["member_puuids"])) == row["size"] for row in squad_summary(_games(), rows, "player"))
